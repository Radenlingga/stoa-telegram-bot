# Stoa Quote Bot — Telegram bot for Robinhood Chain quotes & safety checks

A Telegram bot that answers **/quote** and **/check** for any Robinhood Chain
token, using [Stoa](https://stoadex.com)'s public HTTP API. Every quote links
to the swap on Stoa with the operator's address set as referrer
(`trade?ref=…` — the mechanism documented at
https://stoadex.com/docs/referrals), so swaps made through the bot's links
name the operator as referrer on-chain.

Built for Stoa build bounty #1 — "Telegram bot"
(https://stoadex.com/build#bounties).

## Live instance

- Bot: [@Stoatrade_bot](https://t.me/Stoatrade_bot) — live now.
- Public group where it answers commands:
  [STOA TRADE](https://t.me/stoatrade) — try `/quote BULL` or `/check INTC`
  in the group.
- Referrer named in every swap link and API quote:
  `0x893f754C451fe7Edf459eb24abEF473190101446` (see "How it works").

## Commands

| Command | What it does |
| --- | --- |
| `/quote <token> [amount]` | Buy quote: how much of `<token>` you get for `amount` ETH (default 0.1 ETH), with route, price impact and a pre-filled swap link |
| `/quote <sell> <buy> <amount>` | Quote selling `amount` of one token for another, e.g. `/quote BULL USDG 1000` |
| `/check <token>` | Safety check: simulated buy/sell, taxes, round-trip cost, creator & top-10 holder concentration, verdict |
| `/start`, `/help` | Usage instructions |

`<token>` can be a symbol or name from Stoa's token list (e.g. `BULL`, `INTC`)
or any Robinhood Chain contract address (`0x…`).

## How it works

- `stoa.py` — Stoa API client (stdlib only). Endpoints used, per
  https://stoadex.com/docs/api:
  - `GET /api/v1/tokens?chain=robinhood` — token list for symbol resolution (cached 5 min)
  - `GET /api/v1/quote?chain=robinhood&sellToken=…&buyToken=…&amount=…&referrer=…`
  - `GET /api/safety/robinhood/<token>` — token safety check (cached 60 s)
  - Requests are paced and retried with backoff; Stoa's limit is 60 req/min per IP.
- `bot.py` — Telegram long-polling bot (stdlib only, no webhook needed).
  Per-chat throttling protects the shared API limit.
- `format.py` — renders API results as Telegram messages.
- `test_stoa.py` — command-line harness that exercises the Stoa layer without Telegram.

## Run it

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. `cp .env.example .env` and put the token in `.env` as `BOT_TOKEN=...`
   (optionally override `REFERRER=` — defaults to the operator's address in `stoa.py`).
3. `./run.sh` — or install the systemd user unit:

   ```sh
   cp stoa-bot.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now stoa-bot
   ```

4. Add the bot to a public Telegram group. In groups, address commands as
   `/quote` / `/check` (mention form `/quote@YourBot` also works).

## Notes

- Quotes are snapshots (Stoa quotes are valid ~30 s); the bot says so in every reply.
- The bot never handles private keys and never signs anything — swaps happen
  in the Stoa web app, in the user's own wallet.
- Referral earnings: the referrer named in the links earns 10% of Stoa's
  protocol fee on resulting swaps, per https://stoadex.com/docs/referrals.

## License

MIT — see `LICENSE`.
