#!/usr/bin/env python3
"""Stoa Telegram bot — /quote and /check for Robinhood Chain tokens.

Long polling, stdlib only. Bot token comes from the BOT_TOKEN environment
variable (see .env / run.sh). The bot never prints the token.

Bounty context: Stoa build bounty #1 (stoadex.com/build#bounties) — a bot in
a public group answering /quote and /check for any Robinhood Chain token via
Stoa's API, linking each quote to a swap on Stoa that names our referrer
address (see stoa.py for the documented referral mechanics).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from format import HELP_TEXT, esc, format_check, format_quote  # noqa: E402
from stoa import REFERRER, StoaClient, StoaError, to_wei  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("stoa-bot")

TG_BASE = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_QUOTE_ETH = "0.1"


class Telegram:
    def __init__(self, token: str):
        self._token = token

    def call(self, method: str, payload: dict | None = None, timeout: int = 60) -> dict:
        url = TG_BASE.format(token=self._token, method=method)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise RuntimeError(f"Telegram HTTP {e.code}") from e
            raise RuntimeError(
                f"Telegram error {body.get('error_code')}: {body.get('description')}"
            ) from e
        if not body.get("ok"):
            raise RuntimeError(f"Telegram error: {body.get('description')}")
        return body["result"]

    def send(self, chat_id: int, text: str, button: tuple[str, str] | None = None) -> None:
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if button:
            label, url = button
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "url": url}]]
            }
        self.call("sendMessage", payload, timeout=30)


class Bot:
    def __init__(self, tg: Telegram, stoa: StoaClient):
        self.tg = tg
        self.stoa = stoa
        self.username: str | None = None
        self._chat_last: dict[int, float] = {}

    # -- command handlers --------------------------------------------------
    def cmd_quote(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/quote &lt;token&gt; [amount]</code> — see /help")
            return
        try:
            if len(args) >= 3:
                # /quote <sellToken> <buyToken> <amount>
                sell = self.stoa.resolve_token(args[0])
                buy = self.stoa.resolve_token(args[1])
                amount_human = args[2]
                if not sell or not buy:
                    bad = args[0] if not sell else args[1]
                    self.tg.send(chat_id, f"Unknown token: {esc(bad)}. Try a symbol from Stoa's list or a contract address.")
                    return
            else:
                # /quote <token> [ethAmount] — buy the token with native ETH
                buy = self.stoa.resolve_token(args[0])
                if not buy:
                    self.tg.send(chat_id, f"Unknown token: {esc(args[0])}. Try a symbol from Stoa's list or a contract address.")
                    return
                sell = self.stoa.resolve_token("native")
                amount_human = args[1] if len(args) == 2 else DEFAULT_QUOTE_ETH
            amount_wei = to_wei(amount_human, sell.get("decimals", 18))
            if amount_wei <= 0:
                self.tg.send(chat_id, "Amount must be more than zero.")
                return
            q = self.stoa.quote(sell["address"], buy["address"], amount_wei)
            # Fill in symbols for tokens resolved by bare address.
            if not buy.get("symbol"):
                buy["symbol"] = (self.stoa.safety(buy["address"]).get("symbol")
                                 if buy["address"] != "native" else "ETH")
            if not sell.get("symbol"):
                sell["symbol"] = (self.stoa.safety(sell["address"]).get("symbol")
                                  if sell["address"] != "native" else "ETH")
            text, link = format_quote(self.stoa, sell, buy, amount_human, q)
            self.tg.send(chat_id, text, button=("🔀 Swap on Stoa", link))
        except StoaError as e:
            self._stoa_error(chat_id, e)
        except ValueError:
            self.tg.send(chat_id, "That amount doesn't parse — use something like 0.1 or 250.")

    def cmd_check(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/check &lt;token&gt;</code> — see /help")
            return
        try:
            tok = self.stoa.resolve_token(args[0])
            if not tok:
                self.tg.send(chat_id, f"Unknown token: {esc(args[0])}. Try a symbol from Stoa's list or a contract address.")
                return
            if tok["address"] == "native":
                self.tg.send(chat_id, "ETH is the native gas token — nothing to check. Try a token symbol or contract address.")
                return
            s = self.stoa.safety(tok["address"])
            self.tg.send(chat_id, format_check(s, tok.get("symbol")))
        except StoaError as e:
            self._stoa_error(chat_id, e)

    def _stoa_error(self, chat_id: int, e: StoaError) -> None:
        if e.status == 422:
            msg = "No route for that swap right now (no liquidity or pair on Stoa's venues)."
        elif e.status == 429:
            msg = "Stoa's API rate limit hit — try again in a few seconds."
        elif e.status == 400:
            msg = f"Stoa rejected the request: {esc(e)}"
        else:
            msg = "Stoa's API is briefly unavailable — try again in a moment."
        self.tg.send(chat_id, msg)

    # -- update loop ---------------------------------------------------------
    def handle(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or not text.startswith("/"):
            return
        # basic per-chat throttle (protects the shared 60 req/min Stoa limit)
        now = time.time()
        if now - self._chat_last.get(chat_id, 0) < 1.5:
            return
        self._chat_last[chat_id] = now
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        if cmd in ("/start", "/help"):
            self.tg.send(chat_id, HELP_TEXT)
        elif cmd == "/quote":
            self.cmd_quote(chat_id, args)
        elif cmd == "/check":
            self.cmd_check(chat_id, args)
        # unknown commands are ignored quietly (busy groups)

    def run(self) -> None:
        me = self.tg.call("getMe")
        self.username = me.get("username")
        log.info("Bot @%s running (long polling)", self.username)
        self.tg.call(
            "setMyCommands",
            {"commands": [
                {"command": "quote", "description": "Quote a Robinhood Chain token (buy with ETH, or TOKEN TOKEN amount)"},
                {"command": "check", "description": "Safety-check a Robinhood Chain token"},
                {"command": "start", "description": "Start / how to use this bot"},
                {"command": "help", "description": "How to use this bot"},
            ]},
        )
        offset = 0
        while True:
            try:
                updates = self.tg.call(
                    "getUpdates",
                    {"offset": offset, "timeout": 50, "allowed_updates": ["message"]},
                    timeout=70,
                )
            except Exception as e:  # network hiccup — back off and keep polling
                log.warning("getUpdates failed: %s", e)
                time.sleep(5)
                continue
            for upd in updates:
                offset = max(offset, upd["update_id"] + 1)
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                try:
                    self.handle(msg)
                except Exception:
                    log.exception("handler failed")


def main() -> int:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        print("BOT_TOKEN is not set — copy .env.example to .env and add the token from @BotFather.", file=sys.stderr)
        return 2
    referrer = os.environ.get("REFERRER", REFERRER).strip() or REFERRER
    bot = Bot(Telegram(token), StoaClient(referrer=referrer))
    bot.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
