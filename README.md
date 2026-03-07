# Slumggol Bot

Telegram fact-check bot scaffold built around:

- `Telegram Bot API` for transport
- `GPT-5.4` for single-pass candidate fact-checking
- `gpt-4o-transcribe` for voice notes
- Postgres for authoritative bot state
- Redis for queues and hot claim caches
- ClickHouse Cloud for append-only analytics and outbreak rollups

## Quickstart

1. Copy `.env.example` to `.env`.
2. Set `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and optionally `TELEGRAM_WEBHOOK_SECRET` in `.env`.
3. Start the full local stack with Docker:

```bash
docker compose up --build
```

4. If you also want Docker to expose the local API to Telegram with a Cloudflare quick tunnel and keep the Telegram webhook updated automatically, start the optional tunnel profile:

```bash
docker compose --profile tunnel up --build -d --remove-orphans
```

5. Or, if you prefer to run only Postgres and Redis in Docker, start local dependencies:

```bash
docker compose up -d postgres redis
```

6. Install dependencies:

```bash
uv sync
```

7. Run the database migration:

```bash
uv run alembic upgrade head
```

8. Start the API:

```bash
uv run slumggol-api
```

9. Create a Telegram bot with BotFather, disable bot privacy for groups with `/setprivacy`, and add the bot to the target Telegram group or supergroup.

10. Expose the API on a public HTTPS URL, set `PUBLIC_WEBHOOK_URL` in `.env`, then register the webhook:

```bash
./scripts/set_telegram_webhook.sh
```

11. Start the worker:

```bash
uv run slumggol-worker
```

## Notes

- The app is designed to run without ClickHouse in local development. Analytics failures must never block replies.
- Raw inbound text, image bytes, audio bytes, and transcripts are processed in memory and not persisted.
- ClickHouse schemas and materialized views live in `sql/clickhouse_bot_analytics.sql`.
- Telegram uses a webhook in this app, so it must reach a public HTTPS endpoint; local-only `localhost` webhooks will not work.
- The optional `cloudflared` Compose profile is only there to expose `api:8000` to Telegram during local development.
- The optional `webhook-sync` Compose service watches the Cloudflare quick-tunnel URL and re-registers the Telegram webhook automatically after tunnel restarts.
- `./scripts/set_telegram_webhook.sh` registers the bot against `${PUBLIC_WEBHOOK_URL}/webhooks/telegram`.
- `./scripts/get_cloudflare_tunnel_url.sh` prints the current Cloudflare quick-tunnel URL from Docker logs.
- `./scripts/debug_telegram.sh` calls `getMe` and `getWebhookInfo` for the configured bot token.
