"""Render Stoa API results as Telegram-ready text (HTML parse mode)."""

from __future__ import annotations

import html

from stoa import StoaClient, from_wei


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def _trim(amount: str) -> str:
    """Trim long fractional tails for display (6 dp max)."""
    if "." not in amount:
        return amount
    whole, frac = amount.split(".", 1)
    frac = frac.rstrip("0")
    if len(frac) > 6:
        frac = frac[:6].rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def format_quote(
    client: StoaClient,
    sell: dict,
    buy: dict,
    amount_human: str,
    q: dict,
) -> tuple[str, str]:
    """Returns (text, swap_url)."""
    amount_out = _trim(from_wei(q["amountOut"], buy.get("decimals", 18)))
    min_out = _trim(from_wei(q["minOut"], buy.get("decimals", 18)))
    sell_sym = sell.get("symbol") or "ETH"
    buy_sym = buy.get("symbol") or "token"
    in_wei = int(q["amountIn"])
    out_wei = int(q["amountOut"])
    unit_price = ""
    if in_wei > 0:
        rate = (out_wei / 10 ** buy.get("decimals", 18)) / (
            in_wei / 10 ** sell.get("decimals", 18)
        )
        rate_s = f"{rate:,.10f}".rstrip("0").rstrip(".")
        unit_price = f"\nRate: 1 {esc(sell_sym)} ≈ {rate_s} {esc(buy_sym)}"
    legs = (q.get("route") or {}).get("legs") or []
    venues = ", ".join(
        dict.fromkeys(leg.get("name") or "venue" for leg in legs)
    ) or "—"
    impact = q.get("priceImpactPct")
    impact_s = f"{impact:.4g}%" if isinstance(impact, (int, float)) else "—"
    link = client.swap_link(
        sell["address"], buy["address"], amount_human
    )
    text = (
        f"💱 <b>{esc(amount_human)} {esc(sell_sym)} → {esc(amount_out)} {esc(buy_sym)}</b>\n"
        f"Min received: {esc(min_out)} {esc(buy_sym)} (0.5% slippage)"
        f"{unit_price}\n"
        f"Route: {esc(venues)} · Price impact: {esc(impact_s)}\n"
        f"Quote valid ~{q.get('validForSeconds', 30)}s — re-check before swapping."
    )
    return text, link


_VERDICT_EMOJI = {
    "ok": "✅",
    "caution": "⚠️",
    "warning": "⚠️",
    "danger": "🚫",
    "blocked": "🚫",
}


def format_check(s: dict, fallback_symbol: str | None = None) -> str:
    verdict = str(s.get("verdict", "unknown"))
    emoji = _VERDICT_EMOJI.get(verdict.lower(), "❔")
    sym = s.get("symbol") or fallback_symbol or "token"
    test = s.get("test") or {}
    lines = [f"{emoji} <b>{esc(sym)} safety check — verdict: {esc(verdict)}</b>"]
    addr = s.get("token")
    if addr:
        lines.append(f"<code>{esc(addr)}</code>")
    buy_ok = test.get("buyOk")
    sell_ok = test.get("sellOk")
    if buy_ok is not None or sell_ok is not None:
        lines.append(
            f"Buy: {'ok' if buy_ok else 'FAILS'} · Sell: {'ok' if sell_ok else 'FAILS'}"
        )
    if test.get("buyTaxPct") is not None or test.get("sellTaxPct") is not None:
        lines.append(
            f"Taxes: buy {test.get('buyTaxPct')}%, sell {test.get('sellTaxPct')}%"
        )
    if test.get("roundTripPct") is not None:
        lines.append(f"Round trip (buy+sell): {test['roundTripPct']}%")
    if s.get("creatorPct") is not None:
        lines.append(f"Creator holds: {s['creatorPct']}% of supply")
    if s.get("top10Pct") is not None:
        lines.append(f"Top 10 holders: {s['top10Pct']}% of supply")
    if test.get("why"):
        lines.append(f"Note: {esc(test['why'])}")
    for note in s.get("notes") or []:
        lines.append(f"• {esc(note)}")
    lines.append("Source: Stoa token safety check (simulated buy/sell on-chain).")
    return "\n".join(lines)


HELP_TEXT = (
    "<b>Stoa Quote Bot</b> — quotes &amp; safety checks for Robinhood Chain tokens, via Stoa.\n\n"
    "<b>/quote &lt;token&gt; [amount]</b> — buy a token with ETH (default 0.1 ETH)\n"
    "Examples:\n"
    "• <code>/quote BULL</code>\n"
    "• <code>/quote BULL 0.5</code> — buy with 0.5 ETH\n"
    "• <code>/quote 0x3076…3eff</code> — by contract address\n"
    "• <code>/quote BULL USDG 1000</code> — sell 1000 BULL for USDG\n\n"
    "<b>/check &lt;token&gt;</b> — safety check (taxes, can it be sold, holder concentration)\n"
    "Example: <code>/check BULL</code>\n\n"
    "Every quote links to the swap on Stoa. Prices move fast — the quote is a snapshot."
)
