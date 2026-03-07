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

: "${EVOLUTION_BASE_URL:?EVOLUTION_BASE_URL is required}"
: "${EVOLUTION_API_KEY:?EVOLUTION_API_KEY is required}"
: "${EVOLUTION_INSTANCE:?EVOLUTION_INSTANCE is required}"

phone_raw="${1:-${WHATSAPP_PAIRING_NUMBER:-}}"
if [[ -z "$phone_raw" ]]; then
  echo "Provide a WhatsApp number as the first argument or set WHATSAPP_PAIRING_NUMBER in .env." >&2
  exit 1
fi

phone_number="$(printf '%s' "$phone_raw" | tr -cd '0-9')"
if [[ ${#phone_number} -lt 8 || ${#phone_number} -gt 15 ]]; then
  echo "WHATSAPP_PAIRING_NUMBER must be digits only, with country code." >&2
  exit 1
fi

api_get() {
  curl -fsS -H "apikey: $EVOLUTION_API_KEY" "$1"
}

api_post() {
  curl -fsS \
    -H "apikey: $EVOLUTION_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$2" \
    "$1"
}

instance_json="$(api_get "$EVOLUTION_BASE_URL/instance/fetchInstances?instanceName=$EVOLUTION_INSTANCE")"
instance_count="$(jq 'length' <<<"$instance_json")"

if [[ "$instance_count" -eq 0 ]]; then
  echo "Creating instance $EVOLUTION_INSTANCE..."
  create_payload="$(jq -nc \
    --arg instance_name "$EVOLUTION_INSTANCE" \
    --arg number "$phone_number" \
    '{
      instanceName: $instance_name,
      integration: "WHATSAPP-BAILEYS",
      qrcode: true,
      number: $number
    }')"
  response="$(api_post "$EVOLUTION_BASE_URL/instance/create" "$create_payload")"
else
  echo "Connecting existing instance $EVOLUTION_INSTANCE..."
  response="$(api_get "$EVOLUTION_BASE_URL/instance/connect/$EVOLUTION_INSTANCE?number=$phone_number")"
fi

pairing_code="$(jq -r '.qrcode.pairingCode // empty' <<<"$response")"

for _ in 1 2 3 4 5; do
  if [[ -n "$pairing_code" ]]; then
    break
  fi

  sleep 2
  response="$(api_get "$EVOLUTION_BASE_URL/instance/connect/$EVOLUTION_INSTANCE?number=$phone_number")"
  pairing_code="$(jq -r '.qrcode.pairingCode // empty' <<<"$response")"
done

if [[ -n "$pairing_code" ]]; then
  formatted_code="${pairing_code:0:4}-${pairing_code:4:4}"
  echo
  echo "Pairing code: $formatted_code"
  exit 0
fi

echo
echo "Evolution did not return a pairing code."
echo "Last response:"
jq '.' <<<"$response"

echo
echo "Current connection state:"
api_get "$EVOLUTION_BASE_URL/instance/connectionState/$EVOLUTION_INSTANCE" | jq '.'
