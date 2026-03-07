#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo ".env not found in $ROOT_DIR" >&2
  exit 1
fi

set -a
source .env
set +a

: "${TELEGRAM_BASE_URL:?TELEGRAM_BASE_URL is required}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"

echo "== Bot =="
curl -fsS "${TELEGRAM_BASE_URL%/}/bot${TELEGRAM_BOT_TOKEN}/getMe"

echo
echo
echo "== Webhook =="
curl -fsS "${TELEGRAM_BASE_URL%/}/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
