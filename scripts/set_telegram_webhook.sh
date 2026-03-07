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
: "${PUBLIC_WEBHOOK_URL:?PUBLIC_WEBHOOK_URL is required}"

webhook_url="${PUBLIC_WEBHOOK_URL%/}/webhooks/telegram"

payload="$(jq -nc \
  --arg url "$webhook_url" \
  --arg secret "${TELEGRAM_WEBHOOK_SECRET:-}" \
  '{
    url: $url,
    allowed_updates: ["message"],
    drop_pending_updates: false
  } + (if $secret == "" then {} else {secret_token: $secret} end)')"

curl -fsS \
  -H "Content-Type: application/json" \
  -d "$payload" \
  "${TELEGRAM_BASE_URL%/}/bot${TELEGRAM_BOT_TOKEN}/setWebhook"

echo
echo "Webhook set to $webhook_url"
