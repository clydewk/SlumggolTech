# Slumggol Bot

WhatsApp fact-check bot scaffold built around:

- `Evolution API` for WhatsApp transport
- `GPT-5.4` for single-pass candidate fact-checking
- `gpt-4o-transcribe` for voice notes
- Postgres for authoritative bot state
- Redis for queues and hot claim caches
- ClickHouse Cloud for append-only analytics and outbreak rollups

## Quickstart

1. Copy `.env.example` to `.env` and set the API credentials.
2. Start local dependencies:

```bash
docker compose up -d
```

3. Install dependencies:

```bash
uv sync
```

4. Run the database migration:

```bash
uv run alembic upgrade head
```

5. Start the API:

```bash
uv run slumggol-api
```

6. Start the worker:

```bash
uv run slumggol-worker
```

## Notes

- The app is designed to run without ClickHouse in local development. Analytics failures must never block replies.
- Raw inbound text, image bytes, audio bytes, and transcripts are processed in memory and not persisted.
- ClickHouse schemas and materialized views live in `sql/clickhouse_bot_analytics.sql`.
