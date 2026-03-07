#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo ".env not found in $ROOT_DIR" >&2
  exit 1
fi

env_telegram_base_url="${TELEGRAM_BASE_URL:-}"
env_telegram_bot_token="${TELEGRAM_BOT_TOKEN:-}"
env_telegram_webhook_secret="${TELEGRAM_WEBHOOK_SECRET:-}"
env_public_webhook_url="${PUBLIC_WEBHOOK_URL:-}"

set -a
source .env
set +a

TELEGRAM_BASE_URL="${env_telegram_base_url:-${TELEGRAM_BASE_URL:-}}"
TELEGRAM_BOT_TOKEN="${env_telegram_bot_token:-${TELEGRAM_BOT_TOKEN:-}}"
TELEGRAM_WEBHOOK_SECRET="${env_telegram_webhook_secret:-${TELEGRAM_WEBHOOK_SECRET:-}}"
PUBLIC_WEBHOOK_URL="${env_public_webhook_url:-${PUBLIC_WEBHOOK_URL:-}}"

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
