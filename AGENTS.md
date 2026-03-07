# Slumggol Bot Agent Guide

## Purpose

This repository contains a Telegram fact-check bot for multilingual Singapore group chats. The bot should remain mostly dormant, detect likely misinformation candidates, run a single GPT-5.4 fact-check call for those candidates, and post a short corrective reply only when confidence and corroboration thresholds are met.

## Architecture Rules

- Postgres is the source of truth for bot state, group configuration, cached fact-check results, and admin controls.
- Redis is used for background jobs and hot-claim prewarming.
- ClickHouse Cloud is analytics-only. It is never the source of truth for synchronous bot behavior.
- `Telegram Bot API` is the transport adapter for this codebase.
- `GPT-5.4` is the only fact-check model in the normal path.
- `gpt-4o-transcribe` is the only voice-note transcription model in the normal path.
- Raw inbound content must never be persisted. Only hashes, derived claims, style aggregates, analytics events, bot replies, and usage metrics may be stored.
- No standalone OCR microservice belongs in v1. Images go straight into the GPT-5.4 request.

## Operational Rules

- Default `analysis_mode` is `gated`.
- Default local-development Telegram ingress is `polling`. Webhook plus Cloudflare tunnel is optional.
- `analysis_mode=all_messages_llm` is demo-only, and must auto-expire after the configured TTL or spend cap.
- All `/admin/*` routes require `Authorization: Bearer <ADMIN_API_TOKEN>`.
- Auto-replies require:
  - verdict in `false`, `misleading`, or `unsupported`
  - confidence `>= 0.82`
  - at least 2 corroborating sources
  - one official or Singapore-first source for public-safety and public-health claims
- Analytics failures must fail open and never block ingestion or replies.
- ClickHouse outages must degrade to “no analytics writes” and “no outbreak refresh,” not “no bot.”
- Outbreak refresh is scheduled through ARQ cron using `OUTBREAK_REFRESH_INTERVAL_MINUTES`.
- Metabase is the default internal dashboard surface for the ClickHouse analytics layer; do not build a separate custom dashboard in this repo unless explicitly asked.

## Repo Map

- `src/slumggol_bot/api/`: FastAPI application and HTTP routes
- `src/slumggol_bot/transport/`: Telegram transport abstractions and adapter
- `src/slumggol_bot/services/`: bot logic, hashing, analytics, fact-checking, style profile updates, and outbreak logic
- `src/slumggol_bot/db/`: SQLAlchemy models, sessions, and repositories
- `src/slumggol_bot/workers/`: ARQ worker entrypoints
- `src/slumggol_bot/prompts/`: model prompt templates
- `src/slumggol_bot/sources/registry.yml`: curated Singapore-first source registry
- `scripts/manage_clickhouse.py`: ClickHouse ping/bootstrap/migrate/smoke utility
- `sql/clickhouse_bot_analytics.sql`: ClickHouse DDL and materialized views
- `sql/clickhouse_bot_analytics_migrate_v2.sql`: ClickHouse upgrade path for existing services
- `docker-compose.yml`: optional Metabase dashboard profile plus local infra
- `tests/`: unit and integration tests

## Commands

- `uv sync`: install dependencies
- `docker compose up -d`: start Postgres and Redis locally
- `uv run alembic upgrade head`: run Postgres migrations
- `uv run slumggol-api`: start the API
- `uv run slumggol-poller`: start Telegram polling for local development
- `uv run slumggol-worker`: start the worker
- `uv run python scripts/manage_clickhouse.py ping`: validate ClickHouse auth and database presence
- `uv run python scripts/manage_clickhouse.py bootstrap`: apply the full ClickHouse schema to a fresh service
- `uv run python scripts/manage_clickhouse.py migrate_v2`: upgrade an existing ClickHouse service to the current schema
- `uv run python scripts/manage_clickhouse.py smoke`: verify required ClickHouse objects exist
- `docker compose --profile polling --profile dashboard up --build`: start the bot stack with the optional Metabase dashboard
- `uv run pytest`: run tests
- `uv run mypy src`: run static typing checks
- `uv run ruff check .`: run lint checks

## Coding Standards

- Use typed Python throughout. Avoid untyped public functions.
- Keep business logic in `services/`, not inside route handlers.
- Keep transport-specific logic inside `transport/`.
- Add concise comments only where the code is non-obvious.
- Do not persist raw inbound text or media to Postgres, Redis, or ClickHouse.
- Schema changes must go through Alembic.
- Prompt changes must update tests or fixtures that validate the prompt contract.

## ClickHouse Rules

- ClickHouse tables only contain hashes, derived metadata, bot outputs, and usage events.
- Store dashboard-safe intelligence fields in ClickHouse, including `group_display_name`, `claim_category`, `risk_level`, `actionability`, `has_official_sg_source`, and `official_source_domain_count`.
- Raw analytics tables retain 30 days of data.
- Hourly rollups retain 180 days of data.
- Daily rollups retain 2 years of data.
- Materialized view changes must be accompanied by a matching update in `sql/clickhouse_bot_analytics.sql`.
- Existing ClickHouse services must be upgraded with `sql/clickhouse_bot_analytics_migrate_v2.sql` or `scripts/manage_clickhouse.py migrate_v2` when rollups or views change.
- The ClickHouse dashboard surface is read-only and should come from curated views and query endpoints, not ad hoc writes or synchronous Postgres dependencies.
- Local development may run without ClickHouse; any code that writes analytics must tolerate the sink being disabled.

## Testing Rules

- Mock OpenAI calls in unit tests.
- Mock Telegram Bot API calls in unit tests.
- Verify admin routes reject missing or invalid bearer tokens.
- Verify that analytics failures do not change the reply path.
- Verify that hot-claim refresh does not create duplicate live replies.
- Verify that analytics events use source message time for trendable ClickHouse data.
- Verify that structured claim intelligence fields flow through fact-checking, caching, analytics, and dashboard queries.
- Verify that no raw inbound content is persisted by repository or analytics paths.
