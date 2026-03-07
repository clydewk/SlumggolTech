# Slumggol Bot

WhatsApp fact-check bot scaffold built around:

- `Evolution API` for WhatsApp transport
- `GPT-5.4` for single-pass candidate fact-checking
- `gpt-4o-transcribe` for voice notes
- Postgres for authoritative bot state
- Redis for queues and hot claim caches
- ClickHouse Cloud for append-only analytics and outbreak rollups

## Quickstart

1. Copy `.env.example` to `.env`.
2. Set `EVOLUTION_API_KEY` in `.env` (used by both the bot and local Evolution API).
3. Start local dependencies (Postgres, Redis, Evolution API):

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

7. Create a WhatsApp instance in Evolution API:

```bash
curl -X POST "http://localhost:8080/instance/create" \
  -H "apikey: <your-evolution-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"instanceName":"slumggol","integration":"WHATSAPP-BAILEYS","qrcode":true}'
```

8. Set webhook to this API:

```bash
curl -X POST "http://localhost:8080/webhook/set/slumggol" \
  -H "apikey: <your-evolution-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"url":"http://host.docker.internal:8000/webhooks/evolution","webhook_by_events":true,"events":["MESSAGES_UPSERT"]}'
```

9. Start the worker:

```bash
uv run slumggol-worker
```

## Notes

- The app is designed to run without ClickHouse in local development. Analytics failures must never block replies.
- Raw inbound text, image bytes, audio bytes, and transcripts are processed in memory and not persisted.
- ClickHouse schemas and materialized views live in `sql/clickhouse_bot_analytics.sql`.
- Scan the QR code from the Evolution API instance status endpoint or manager UI after instance creation.
