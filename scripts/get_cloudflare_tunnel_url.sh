#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

for _ in $(seq 1 30); do
  url="$(
    docker compose logs --no-color cloudflared 2>/dev/null \
      | sed -nE 's|.*(https://[a-z0-9-]+\.trycloudflare\.com).*|\1|p' \
      | tail -n 1
  )"
  if [[ -n "$url" ]]; then
    printf '%s\n' "$url"
    exit 0
  fi

  sleep 1
done

echo "Cloudflare tunnel URL not found in docker compose logs." >&2
exit 1
