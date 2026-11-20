"""Stoa API client (Robinhood Chain) — stdlib only.

Endpoints (from https://stoadex.com/docs/api, verified live 2026-11-20):
  GET https://stoadex.com/api/v1/tokens?chain=robinhood
  GET https://stoadex.com/api/v1/quote?chain=robinhood&sellToken=..&buyToken=..
        &amount=<wei>&referrer=<address>
  GET https://stoadex.com/api/safety/robinhood/<tokenAddress>

Referral mechanics (from https://stoadex.com/docs/referrals):
  * API: pass `referrer=<address>` to /quote; the swap built from the quote
    names that address as referrer on-chain.
  * Web: https://stoadex.com/trade?ref=<address> makes the visitor's browser
    remember the address as referrer for every swap in the app. The trade page
    also accepts chain / sell / buy / amount query params to pre-fill a swap.
  * Referrer earns 10% of Stoa's 2 bps protocol fee (per current docs).

Rate limit: 60 requests/minute per IP — this client paces calls and caches.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://stoadex.com/api/v1"
SAFETY_BASE = "https://stoadex.com/api/safety"
CHAIN = "robinhood"
REFERRER = "0x893f754C451fe7Edf459eb24abEF473190101446"
NATIVE = "native"  # ETH on Robinhood Chain

_UA = {
    "User-Agent": "stoa-telegram-bot/1.0 (+https://stoadex.com/build)",
    # The API's chunked responses get cut mid-body by an intermediary
    # (http.client.IncompleteRead); identity encoding avoids chunking.
    "Accept-Encoding": "identity",
}


_TOKENS_DISK = Path(__file__).with_name("tokens-cache.json")


class StoaError(Exception):
    """An error reply from the Stoa API (or a transport failure)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class StoaClient:
    def __init__(self, referrer: str = REFERRER, min_interval: float = 1.1):
        self.referrer = referrer
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._tokens_cache: tuple[float, dict] | None = None
        self._safety_cache: dict[str, tuple[float, dict]] = {}

    # -- transport ---------------------------------------------------------
    # Two transports, one contract: return (status, body_text); raise only
    # on transport failure. urllib is primary. Since 2026-11-21 the API (or
    # an intermediary) intermittently closes urllib's connections before
    # the body arrives (IncompleteRead, 0 bytes) while curl to the same URL
    # succeeds — so a transport failure in urllib falls back to curl
    # within the same attempt.
    def _fetch_urllib(self, url: str) -> tuple[int, str]:
        req = urllib.request.Request(url, headers=_UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def _fetch_curl(self, url: str) -> tuple[int, str]:
        proc = subprocess.run(
            ["curl", "-s", "-m", "30",
             "-H", f"User-Agent: {_UA['User-Agent']}",
             "-H", "Accept-Encoding: identity",
             "-w", "\n__HTTP__%{http_code}", url],
            capture_output=True, text=True, timeout=40,
        )
        body, _, status = proc.stdout.rpartition("\n__HTTP__")
        if proc.returncode != 0 or not status.isdigit():
            raise StoaError(f"curl transport failed: {proc.stderr.strip()}")
        return int(status), body

    def _get(self, url: str, retries: int = 2) -> dict:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            with self._lock:
                wait = self._min_interval - (time.time() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.time()
            try:
                try:
                    status, text = self._fetch_urllib(url)
                except Exception:
                    status, text = self._fetch_curl(url)
            except Exception as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise StoaError(f"Stoa API unreachable: {e}") from e
            if status == 200:
                try:
                    return json.loads(text)
                except json.JSONDecodeError as e:
                    last_exc = e
                    if attempt < retries:
                        time.sleep(2 * (attempt + 1))
                        continue
                    raise StoaError(f"Stoa API unreachable: {e}") from e
            try:
                body = json.loads(text)
                msg = body.get("error") or str(body)
            except Exception:
                msg = f"HTTP {status}"
            if status == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise StoaError(str(msg), status=status)
        raise StoaError(f"Stoa API unreachable: {last_exc}")

    # -- token list --------------------------------------------------------
    def tokens(self) -> dict:
        """Cached token list for Robinhood Chain (5 min TTL).

        The last good list is also kept on disk (tokens-cache.json) so a
        bot restart during an API wobble still resolves symbols from the
        stale list instead of failing every command.
        """
        now = time.time()
        if self._tokens_cache and now - self._tokens_cache[0] < 300:
            return self._tokens_cache[1]
        try:
            data = self._get(f"{API_BASE}/tokens?chain={CHAIN}")
        except StoaError:
            # A stale list beats no answer: token metadata changes slowly.
            if self._tokens_cache:
                return self._tokens_cache[1]
            disk = self._tokens_from_disk()
            if disk is not None:
                self._tokens_cache = (0.0, disk)
                return disk
            raise
        chain_info = next(
            (c for c in data.get("chains", []) if c.get("chain") == CHAIN), {}
        )
        self._tokens_cache = (now, chain_info)
        try:
            _TOKENS_DISK.write_text(json.dumps(
                {"fetched_at": now, "chain_info": chain_info}))
        except Exception:
            pass
        return chain_info

    @staticmethod
    def _tokens_from_disk() -> dict | None:
        try:
            saved = json.loads(_TOKENS_DISK.read_text())
            info = saved.get("chain_info")
            return info if isinstance(info, dict) and info else None
        except Exception:
            return None

    def resolve_token(self, query: str) -> dict | None:
        """Resolve an address, symbol or name to a token dict.

        Returns {"address", "symbol", "name", "decimals"} or None.
        Addresses not in Stoa's list still resolve (decimals unknown -> 18,
        symbol fetched from the safety endpoint by the caller if needed).
        """
        q = query.strip()
        if q.lower() in ("eth", "native") and q.lower() != "weth":
            info = self.tokens()
            native = info.get("native", {})
            return {
                "address": NATIVE,
                "symbol": native.get("symbol", "ETH"),
                "name": "Ether (native)",
                "decimals": 18,
            }
        if q.startswith("0x") and len(q) == 42:
            info = self.tokens()
            for tok in info.get("tokens", []):
                if tok["address"].lower() == q.lower():
                    return dict(tok)
            return {"address": q, "symbol": None, "name": None, "decimals": 18}
        info = self.tokens()
        tokens = info.get("tokens", [])
        for tok in tokens:  # exact symbol match first
            if tok["symbol"].lower() == q.lower():
                return dict(tok)
        for tok in tokens:  # then name / symbol substring
            if q.lower() in tok["name"].lower() or q.lower() in tok["symbol"].lower():
                return dict(tok)
        return None

    # -- quote -------------------------------------------------------------
    def quote(self, sell_token: str, buy_token: str, amount_wei: int) -> dict:
        params = {
            "chain": CHAIN,
            "sellToken": sell_token,
            "buyToken": buy_token,
            "amount": str(amount_wei),
            "referrer": self.referrer,
        }
        url = f"{API_BASE}/quote?{urllib.parse.urlencode(params)}"
        return self._get(url)

    # -- safety check ------------------------------------------------------
    def safety(self, token_address: str) -> dict:
        key = token_address.lower()
        now = time.time()
        cached = self._safety_cache.get(key)
        if cached and now - cached[0] < 60:
            return cached[1]
        data = self._get(f"{SAFETY_BASE}/{CHAIN}/{token_address}")
        self._safety_cache[key] = (now, data)
        return data

    # -- links -------------------------------------------------------------
    def swap_link(self, sell_token: str, buy_token: str, amount_human: str | None = None) -> str:
        """Trade-page link that pre-fills the swap and names our referrer.

        The trade page stores `ref` as the visitor's referrer (verified in the
        app code and the referrals docs), and reads chain/sell/buy/amount to
        pre-select the pair.
        """
        params = {"chain": CHAIN, "sell": sell_token, "buy": buy_token, "ref": self.referrer}
        if amount_human:
            params["amount"] = amount_human
        return "https://stoadex.com/trade?" + urllib.parse.urlencode(params)


# -- amount helpers ----------------------------------------------------------

def to_wei(amount_human: str, decimals: int) -> int:
    """Convert a human amount string ('0.1', '250') to the smallest unit."""
    if "." in amount_human:
        whole, frac = amount_human.split(".", 1)
    else:
        whole, frac = amount_human, ""
    frac = (frac + "0" * decimals)[:decimals]
    return int(whole or "0") * (10**decimals) + int(frac or "0")


def from_wei(amount_wei: int | str, decimals: int) -> str:
    """Format a smallest-unit amount as a trimmed human string."""
    v = int(amount_wei)
    sign = "-" if v < 0 else ""
    v = abs(v)
    whole, frac = divmod(v, 10**decimals)
    if frac == 0:
        return f"{sign}{whole:,}"
    frac_s = str(frac).rjust(decimals, "0").rstrip("0")
    return f"{sign}{whole:,}.{frac_s}"
