# IsRealANot Agent Guide

## Purpose

This repository contains a Telegram fact-check bot for Singapore group chats. The implemented bot stays mostly dormant, uses heuristic gating plus hot-claim reuse to pick candidates, runs GPT-5.4 fact-checks on those candidates, and posts a corrective reply only when stricter reply thresholds pass. It also supports manual `/factcheck` checks, in-thread follow-up questions, per-message translation buttons, and an optional Sea-Lion language-assist layer for Southeast Asian phrasing.

## Architecture Rules

- Postgres is the source of truth for group state, style profiles, cached fact-check results, hot-claim snapshots, and the escalation queue.
- Redis is used for ARQ worker coordination and transient state only: rate limits, hash/simhash observations, hot-claim prewarming, and translation state.
- ClickHouse Cloud is analytics-only. It powers rollups, dashboard queries, and outbreak discovery, but it is never the source of truth for synchronous bot behavior.
- `Telegram Bot API` is the only transport adapter in this repo. FastAPI handles webhook ingress, and `slumggol-poller` handles polling ingress.
- `GPT-5.4` is the only model that produces fact-check verdicts, follow-up answers, and translations on the normal path.
- Optional `Sea-Lion` assistance may be used only as a paraphrase or interpretation aid for Southeast Asian phrasing. It must never supply verdicts, evidence, or replace the GPT-5.4 fact-check path.
- `gpt-4o-transcribe` is the only voice-note transcription model on the normal path.
- Raw inbound text, image bytes, audio bytes, and transcripts must not be persisted. Persist only hashes, derived claims, style aggregates, bot outputs, escalation records, and analytics-safe metadata.
- Images go directly into the GPT request. There is no separate OCR service in this codebase.
- Group tone adaptation lives in `services/style_profiles.py`; keep reply-style logic there rather than inside routes or transport code.

## Current Behavior

- Default group `analysis_mode` is `gated`.
- `analysis_mode=all_messages_llm` exists and is wired through the gate, but the configured demo TTL and spend-cap settings are not currently auto-enforced anywhere in the code.
- Default local Telegram ingress is `polling`. The poller deletes any existing webhook before calling `getUpdates`.
- Webhook mode is supported through `POST /webhooks/telegram` and optionally validates `X-Telegram-Bot-Api-Secret-Token` against `TELEGRAM_WEBHOOK_SECRET`.
- Manual fact-checking works through `/factcheck <claim>` or by replying to a message and mentioning `@<bot_username>`.
- Users can ask follow-up questions by replying to a bot fact-check message; the bot answers in-thread.
- Bot replies include translation buttons. Users can translate a bot message once per target language (`en`, `zh`, `ms`, `ta`), and translation dedupes across the full translation thread root.
- Optional Sea-Lion assist can run for `/factcheck`, forwarded messages, or Southeast Asian language inputs when enabled, but GPT-5.4 still performs the final fact-check.
- Automatic replies currently require:
  - `needs_reply=True`
  - verdict in `false`, `misleading`, or `unsupported`
  - confidence `>= 0.82`
  - at least 2 evidence sources
  - at least one Singapore-first or official source for `public_health` and `public_safety`
- Unclear or unsupported results with confidence `>= 0.5` are queued in the Postgres escalation table instead of auto-replied.
- Auto-replies append source attribution if the model reply omitted it. Stale evidence can append a freshness caveat.
- Redis rate limiting is active in the pipeline:
  - per user per group: 5 messages per 60 seconds
  - per group: 10 messages per 120 seconds
- Analytics writes must fail open and never block ingestion or replies.
- ClickHouse outages must degrade to "no analytics writes" and "no outbreak refresh," not "no bot."
- Outbreak refresh runs through ARQ cron using `OUTBREAK_REFRESH_INTERVAL_MINUTES`, clamped in code to the range `1..60`.
- The optional internal dashboard surface in this repo is Metabase via the Compose `dashboard` profile. There is no custom dashboard frontend here.

## Repo Map

- `src/slumggol_bot/api/app.py`: FastAPI app, dependency wiring, webhook ingress, admin endpoints, and pipeline assembly
- `src/slumggol_bot/polling.py`: polling runner that disables webhooks and feeds updates into the pipeline
- `src/slumggol_bot/transport/telegram.py`: Telegram update normalization, command detection, callback handling, and message send/edit calls
- `src/slumggol_bot/services/pipeline.py`: main orchestration, gating, reply logic, follow-ups, translations, analytics events, and escalation triggers
- `src/slumggol_bot/services/factcheck.py`: OpenAI client, source registry, caching, hot-claim reuse, transcription, follow-up, and translation calls
- `src/slumggol_bot/services/cache.py`: Redis/in-memory hot-claim stores and hash/simhash observation stores
- `src/slumggol_bot/services/analytics.py`: ClickHouse sink, fail-open wrapper, and dashboard/query service
- `src/slumggol_bot/services/outbreak.py`: hot-claim refresh service backed by ClickHouse analytics
- `src/slumggol_bot/services/escalation.py`: escalation policy and repository wrapper
- `src/slumggol_bot/services/freshness.py`: evidence recency scoring and stale-source caveats
- `src/slumggol_bot/services/rate_limit.py`: Redis-backed user/group throttling
- `src/slumggol_bot/services/sealion.py`: optional Sea-Lion client and Southeast Asian language-assist parsing
- `src/slumggol_bot/services/style_profiles.py`: group tone profiling used to steer model replies
- `src/slumggol_bot/services/language.py`: language-conflict prompt helper for multilingual reply versions
- `src/slumggol_bot/services/hashing.py`: text/media hashing and simhash utilities
- `src/slumggol_bot/db/`: SQLAlchemy models, repositories, and session setup
- `src/slumggol_bot/workers/settings.py`: ARQ worker entrypoint and outbreak cron configuration
- `src/slumggol_bot/prompts/factcheck_system.txt`: fact-check prompt contract
- `src/slumggol_bot/sources/registry.yml`: curated Singapore-first source registry
- `alembic/versions/`: Postgres migrations for initial schema, simhash columns, claim-intelligence cache columns, and escalation queue
- `sql/clickhouse_bot_analytics.sql`: canonical ClickHouse bootstrap DDL, rollups, and dashboard views
- `sql/clickhouse_bot_analytics_migrate_v2.sql`: upgrade path for existing ClickHouse services
- `scripts/manage_clickhouse.py`: ClickHouse ping/bootstrap/migrate/smoke tool
- `scripts/set_telegram_webhook.sh`: registers `${PUBLIC_WEBHOOK_URL}/webhooks/telegram`
- `scripts/sync_telegram_webhook.py`: watches the Cloudflare quick-tunnel log and re-registers the webhook after tunnel changes
- `scripts/get_cloudflare_tunnel_url.sh`: prints the current quick-tunnel URL from Docker logs
- `scripts/debug_telegram.sh`: quick `getMe` and `getWebhookInfo` helper
- `docker-compose.yml`: local Postgres/Redis plus API, worker, poller, tunnel, webhook-sync, and optional Metabase profile
- `tests/`: unit and integration-style coverage for transport, pipeline, admin API, analytics, polling, outage behavior, and ClickHouse-adjacent flows
- `article-classifier/`: separate LibreChat MCP proof of concept, not part of the Telegram bot runtime

## Commands

- `uv sync`: install dependencies
- `docker compose up -d postgres redis`: start local Postgres and Redis
- `uv run alembic upgrade head`: run Postgres migrations
- `uv run slumggol-api`: start the FastAPI app
- `uv run slumggol-poller`: start Telegram polling for local development
- `uv run slumggol-worker`: start the ARQ worker
- `docker compose --profile polling up --build`: run the local bot stack in polling mode
- `docker compose --profile polling --profile dashboard up --build`: run the bot stack plus Metabase
- `docker compose --profile tunnel up --build -d --remove-orphans`: run webhook mode with the optional Cloudflare quick tunnel
- `uv run python scripts/manage_clickhouse.py ping`: validate ClickHouse connectivity
- `uv run python scripts/manage_clickhouse.py bootstrap`: apply the full ClickHouse schema to a fresh service
- `uv run python scripts/manage_clickhouse.py migrate_v2`: upgrade an existing ClickHouse service
- `uv run python scripts/manage_clickhouse.py smoke`: verify required ClickHouse objects exist
- `./scripts/set_telegram_webhook.sh`: register the Telegram webhook against `PUBLIC_WEBHOOK_URL`
- `./scripts/debug_telegram.sh`: inspect bot auth and webhook state
- `uv run pytest`: run tests
- `uv run mypy src`: run type checks
- `uv run ruff check .`: run lint checks

## Coding Standards

- Use typed Python throughout. Avoid untyped public functions.
- Keep route handlers thin. Business logic belongs in `services/`.
- Keep Telegram-specific parsing and API calls inside `transport/`.
- Do not persist raw inbound content to Postgres, Redis, or ClickHouse.
- Schema changes must go through Alembic.
- ClickHouse schema or view changes must update both `sql/clickhouse_bot_analytics.sql` and `sql/clickhouse_bot_analytics_migrate_v2.sql`.
- Prompt changes must update any tests or fixtures that validate the prompt contract or parsed response shape.
- Sea-Lion changes must remain auxiliary only: no verdicting, no evidence sourcing, and no replacement of the GPT-5.4 fact-check path.
- If you change reply thresholds, update both configuration and `services/pipeline.should_reply`; the current runtime logic uses hardcoded thresholds rather than `AppSettings.reply_confidence_threshold` and `AppSettings.min_sources_required`.
- If you implement demo-mode expiry or spend caps, wire them into the actual request path; the env settings exist today but are not enforced.
- If you touch admin endpoints, keep bearer-token checks consistent across the entire `/admin/*` surface.

## ClickHouse Rules

- ClickHouse tables must contain only hashes, derived metadata, bot outputs, and usage events.
- Dashboard-safe intelligence fields currently include `group_display_name`, `claim_category`, `risk_level`, `actionability`, `has_official_sg_source`, and `official_source_domain_count`.
- Raw event tables retain 30 days of data:
  - `message_events`
  - `claim_events`
  - `factcheck_events`
  - `reply_events`
  - `usage_events`
- Short-horizon rollups retain 180 days of data:
  - `hash_reuse_1h`
  - `claim_spread_5m`
  - `claim_intel_5m`
- Daily rollups retain 2 years of data:
  - `model_spend_daily`
  - `source_quality_daily`
  - `factcheck_intel_daily`
  - `reply_outcomes_daily`
- Dashboard views currently include:
  - `dashboard_summary_24h`
  - `dashboard_trending_claims_24h`
  - `dashboard_claim_group_spread_24h`
  - `dashboard_high_risk_scams_24h`
- The dashboard surface is read-only and should stay backed by curated views/query endpoints rather than ad hoc writes or synchronous Postgres joins.
- Local development may run with `ENABLE_CLICKHOUSE=false`; analytics code must tolerate the sink being disabled.

## Testing Rules

- Mock OpenAI calls in unit tests.
- Mock Sea-Lion calls in unit tests when touching the auxiliary language-assist path.
- Mock Telegram Bot API calls in unit tests.
- Verify admin routes reject missing or invalid bearer tokens.
- Verify analytics failures do not change the reply path.
- Verify polling deletes webhooks before local polling starts.
- Verify hot-claim exact-hash and simhash reuse avoid unnecessary model calls.
- Verify audio candidates only gain transcript-derived hashes after transcription.
- Verify follow-up replies, translation callbacks, and translation dedupe behavior.
- Verify stale-source caveats and source attribution behavior on replies.
- Verify analytics events use source message timestamps and never include raw inbound text.
- Verify claim-intelligence fields flow through fact-checking, caching, analytics, and dashboard queries.
- Verify no raw inbound content is persisted by repository or analytics paths.
- If you add or modify escalation endpoints, add auth coverage for them; current tests only cover the dashboard and group metrics admin paths.

## Known Drift

- `DEMO_MODE_MAX_SPEND_USD` and `DEMO_MODE_TTL_MINUTES` are present in settings but are not currently enforced by the running pipeline.
- `REPLY_CONFIDENCE_THRESHOLD` and `MIN_SOURCES_REQUIRED` are present in settings but are not currently consumed by `should_reply`.
- The escalation admin endpoints exist, but bearer-token protection is not currently applied to them even though they should be treated as admin-only.
- `NormalizedMessage` has fields for `detected_languages`, `forwarded_many_times`, `media_sha256`, and `image_phash`, but the Telegram transport currently does not populate those fields.
