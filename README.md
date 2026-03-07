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
3. Start local dependencies (Postgres and Redis):

```bash
docker compose up -d
```

4. Install dependencies:

```bash
uv sync
```

5. Run the database migration:

```bash
uv run alembic upgrade head
```

6. Start the API:

```bash
uv run slumggol-api
```

7. Create a Telegram bot with BotFather, disable bot privacy for groups with `/setprivacy`, and add the bot to the target Telegram group or supergroup.

8. Expose the API on a public HTTPS URL, set `PUBLIC_WEBHOOK_URL` in `.env`, then register the webhook:

```bash
./scripts/set_telegram_webhook.sh
```

9. Start the worker:

```bash
uv run slumggol-worker
```

## Notes

- The app is designed to run without ClickHouse in local development. Analytics failures must never block replies.
- Raw inbound text, image bytes, audio bytes, and transcripts are processed in memory and not persisted.
- ClickHouse schemas and materialized views live in `sql/clickhouse_bot_analytics.sql`.
- Telegram requires a publicly reachable HTTPS webhook endpoint; local-only `localhost` webhooks will not work.
- `./scripts/set_telegram_webhook.sh` registers the bot against `${PUBLIC_WEBHOOK_URL}/webhooks/telegram`.
- `./scripts/debug_telegram.sh` calls `getMe` and `getWebhookInfo` for the configured bot token.
