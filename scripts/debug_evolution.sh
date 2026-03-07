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

echo "== Compose Service =="
docker compose ps evolution-api

echo
echo "== API Root =="
curl -sS "$EVOLUTION_BASE_URL/"

echo
echo
echo "== Connection State =="
curl -sS -H "apikey: $EVOLUTION_API_KEY" \
  "$EVOLUTION_BASE_URL/instance/connectionState/$EVOLUTION_INSTANCE"

echo
echo
echo "== Instance Record =="
curl -sS -H "apikey: $EVOLUTION_API_KEY" \
  "$EVOLUTION_BASE_URL/instance/fetchInstances?instanceName=$EVOLUTION_INSTANCE"

echo
echo
echo "== Connect Payload =="
curl -sS -H "apikey: $EVOLUTION_API_KEY" \
  "$EVOLUTION_BASE_URL/instance/connect/$EVOLUTION_INSTANCE"

echo
echo
echo "== Webhook Config =="
curl -sS -H "apikey: $EVOLUTION_API_KEY" \
  "$EVOLUTION_BASE_URL/webhook/find/$EVOLUTION_INSTANCE"

echo
echo
echo "== Recent Logs =="
docker compose logs --no-color --tail=120 evolution-api
