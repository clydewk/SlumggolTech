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
2. Set `OPENAI_API_KEY` and `TELEGRAM_BOT_TOKEN` in `.env`. Leave `TELEGRAM_INGEST_MODE=polling` for the default local-development path.
3. Start the local stack with Docker, including the Telegram poller:

```bash
docker compose --profile polling up --build --remove-orphans
```

If you previously ran tunnel/webhook mode, stop those services first:

```bash
docker compose stop cloudflared webhook-sync
docker compose rm -f cloudflared webhook-sync
```

4. Create a Telegram bot with BotFather, disable bot privacy for groups with `/setprivacy`, and add the bot to the target Telegram group or supergroup.

5. If you prefer to run only Postgres and Redis in Docker, start local dependencies:

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

9. Start the poller:

```bash
uv run slumggol-poller
```

10. Start the worker:

```bash
uv run slumggol-worker
```

## Optional Webhook Mode

If you want webhook delivery instead of polling:

1. Set `TELEGRAM_INGEST_MODE=webhook` in `.env`.
2. Expose the API on a public HTTPS URL, set `PUBLIC_WEBHOOK_URL` in `.env`, then register the webhook:

```bash
./scripts/set_telegram_webhook.sh
```

3. Or, for local development with a Cloudflare quick tunnel and automatic webhook registration, start the optional tunnel profile:

```bash
docker compose --profile tunnel up --build -d --remove-orphans
```

## Notes

- The app is designed to run without ClickHouse in local development. Analytics failures must never block replies.
- Raw inbound text, image bytes, audio bytes, and transcripts are processed in memory and not persisted.
- ClickHouse schemas and materialized views live in `sql/clickhouse_bot_analytics.sql`.
- Polling is the default local-development ingress path because it does not need a public HTTPS endpoint.
- Polling mode and tunnel/webhook mode are mutually exclusive; stop `cloudflared` and `webhook-sync` before polling to prevent webhook re-registration and Telegram `409 Conflict` on `getUpdates`.
- The poller disables any existing Telegram webhook on startup before calling `getUpdates`.
- Webhook mode still requires a public HTTPS endpoint; local-only `localhost` webhooks will not work.
- Manual fact-checking supports both `/factcheck <claim>` and replying to a message with `@<bot_username>` to trigger a check of the replied message.
- Users can continue the thread by replying to a bot fact-check message with follow-up questions; the bot answers in-thread using Telegram reply mode.
- Bot replies include inline translation buttons. Users can translate each bot message once per target language (English, Chinese, Malay, Tamil), and duplicate translation requests for the same message/language are blocked.
- The optional `cloudflared` Compose profile is only there to expose `api:8000` to Telegram during local development when `TELEGRAM_INGEST_MODE=webhook`.
- The optional `webhook-sync` Compose service watches the Cloudflare quick-tunnel URL and re-registers the Telegram webhook automatically after tunnel restarts.
- `./scripts/set_telegram_webhook.sh` registers the bot against `${PUBLIC_WEBHOOK_URL}/webhooks/telegram`.
- `./scripts/get_cloudflare_tunnel_url.sh` prints the current Cloudflare quick-tunnel URL from Docker logs.
- `./scripts/debug_telegram.sh` calls `getMe` and `getWebhookInfo` for the configured bot token.
