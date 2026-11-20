#!/usr/bin/env bash
# Run the Stoa Telegram bot with the token from .env (never printed).
set -euo pipefail
cd "$(dirname "$0")"
set -a
# shellcheck disable=SC1091
source .env
set +a
exec python3 bot.py
