#!/usr/bin/env python3
"""Exercise the Stoa layer from the command line (no Telegram needed).

Usage:
  python3 test_stoa.py quote BULL
  python3 test_stoa.py quote BULL 0.5
  python3 test_stoa.py quote BULL USDG 1000
  python3 test_stoa.py check BULL
  python3 test_stoa.py check 0x30768B5482e5dd8a2487cE0BE61E01fA03Af37ff
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from format import format_check, format_quote  # noqa: E402
from stoa import StoaClient, StoaError, to_wei  # noqa: E402


def strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)


def main(argv: list[str]) -> int:
    client = StoaClient()
    if len(argv) < 3 or argv[1] not in ("quote", "check"):
        print(__doc__)
        return 2
    cmd, args = argv[1], argv[2:]
    try:
        if cmd == "check":
            tok = client.resolve_token(args[0])
            if not tok:
                print(f"unknown token: {args[0]}")
                return 1
            s = client.safety(tok["address"])
            print(strip_html(format_check(s, tok.get("symbol"))))
            return 0
        # quote
        if len(args) >= 3:
            sell = client.resolve_token(args[0])
            buy = client.resolve_token(args[1])
            amount_human = args[2]
        else:
            buy = client.resolve_token(args[0])
            sell = client.resolve_token("native")
            amount_human = args[1] if len(args) == 2 else "0.1"
        if not sell or not buy:
            print("unknown token in pair")
            return 1
        wei = to_wei(amount_human, sell.get("decimals", 18))
        q = client.quote(sell["address"], buy["address"], wei)
        if not buy.get("symbol"):
            buy["symbol"] = client.safety(buy["address"]).get("symbol")
        if not sell.get("symbol"):
            sell["symbol"] = client.safety(sell["address"]).get("symbol")
        text, link = format_quote(client, sell, buy, amount_human, q)
        print(strip_html(text))
        print("Swap link:", link)
        return 0
    except StoaError as e:
        print(f"StoaError (status={e.status}): {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
