# Slumggol Bot

Telegram fact-check bot scaffold built around:

- `Telegram Bot API` for transport
- `GPT-5.4` for single-pass candidate fact-checking
- optional `Sea-Lion` language assist for Southeast Asian phrasing
- `gpt-4o-transcribe` for voice notes
- Postgres for authoritative bot state
- Redis for queues and hot claim caches
- ClickHouse Cloud for append-only analytics, outbreak rollups, and dashboard views
- Metabase as an optional internal dashboard profile

## Quickstart

1. Copy `.env.example` to `.env`.
2. Set `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `ADMIN_API_TOKEN` in `.env`.
3. Leave `TELEGRAM_INGEST_MODE=polling` for the default local-development path.
4. Start local Postgres and Redis:

```bash
docker compose up -d postgres redis
```

5. Install dependencies:

```bash
uv sync
```

6. Run the Postgres migrations:

```bash
uv run alembic upgrade head
```

7. Start the bot stack:

```bash
docker compose --profile polling up --build
```

8. Create a Telegram bot with BotFather, disable bot privacy for groups with `/setprivacy`, and add the bot to the target group or supergroup.

## Optional Sea-Lion Language Assist

Sea-Lion support is optional. When enabled, the bot can use it as a paraphrase aid for Southeast Asian phrasing before GPT-5.4 performs the final fact-check.

```dotenv
SEALION_ENABLED=true
SEALION_API_KEY=replace-with-sea-lion-key
SEALION_BASE_URL=https://api.sea-lion.ai/v1
SEALION_MODEL=aisingapore/Gemma-SEA-LION-v4-27B-IT
SEALION_ASSIST_ON_FACTCHECK_COMMAND=true
SEALION_ASSIST_ON_FORWARDED_MESSAGES=true
```

GPT-5.4 still produces the verdict, evidence, and final reply. Sea-Lion output is used only as an interpretation aid and is never treated as evidence.

## OpenAI Request Tuning

The GPT-5.4 path now supports env-configurable Responses API tuning instead of hardcoded request settings.

Global defaults:

```dotenv
OPENAI_REASONING_EFFORT=medium
OPENAI_VERBOSITY=medium
```

Optional per-task overrides:

```dotenv
OPENAI_FACTCHECK_REASONING_EFFORT=medium
OPENAI_FOLLOWUP_REASONING_EFFORT=minimal
OPENAI_TRANSLATION_REASONING_EFFORT=minimal
OPENAI_FACTCHECK_VERBOSITY=low
OPENAI_FOLLOWUP_VERBOSITY=medium
OPENAI_TRANSLATION_VERBOSITY=low
```

If a per-task override is unset, that request type falls back to the global default.

## ClickHouse Cloud Setup

Use ClickHouse Cloud for analytics and the dashboard views.

1. Set these `.env` values before enabling ClickHouse:

```dotenv
ENABLE_CLICKHOUSE=true
CLICKHOUSE_URL=https://your-clickhouse-host:8443
CLICKHOUSE_DATABASE=bot_analytics
CLICKHOUSE_USER=slumggol_ingest
CLICKHOUSE_PASSWORD=replace-with-verified-password
CLICKHOUSE_ASYNC_INSERT=1
CLICKHOUSE_WAIT_FOR_ASYNC_INSERT=1
OUTBREAK_REFRESH_INTERVAL_MINUTES=5
```

2. Create or verify the database and SQL users in ClickHouse Cloud. Recommended split:
   - ingest user: `SELECT, INSERT` on `bot_analytics.*`
   - dashboard user: `SELECT` on `bot_analytics.*`, `readonly=1`

Example SQL to run in the ClickHouse Cloud SQL console:

```sql
CREATE DATABASE IF NOT EXISTS bot_analytics;

CREATE USER IF NOT EXISTS slumggol_ingest IDENTIFIED BY 'replace-me';
GRANT SELECT, INSERT ON bot_analytics.* TO slumggol_ingest;

CREATE USER IF NOT EXISTS slumggol_dashboard IDENTIFIED BY 'replace-me';
GRANT SELECT ON bot_analytics.* TO slumggol_dashboard;
ALTER USER slumggol_dashboard SETTINGS readonly = 1;
```

3. Validate connectivity:

```bash
uv run python scripts/manage_clickhouse.py ping
```

4. Bootstrap a fresh service:

```bash
uv run python scripts/manage_clickhouse.py bootstrap
```

5. Or migrate an existing service with older rollups:

```bash
uv run python scripts/manage_clickhouse.py migrate_v2
```

6. Run a smoke check:

```bash
uv run python scripts/manage_clickhouse.py smoke
```

The canonical bootstrap DDL lives in `sql/clickhouse_bot_analytics.sql`. The upgrade path for existing services lives in `sql/clickhouse_bot_analytics_migrate_v2.sql`.

## Optional Dashboard Profile

This repo does not include a custom dashboard frontend. Instead, it ships an optional Metabase profile for a fast internal web dashboard.

1. Add the Metabase settings to `.env`:

```dotenv
METABASE_PORT=3000
METABASE_SITE_URL=http://localhost:3000
```

2. Start the local dashboard profile:

```bash
docker compose --profile polling --profile dashboard up --build
```

3. Open `http://localhost:3000` and complete the Metabase admin setup.
4. Add a ClickHouse database connection using the read-only dashboard SQL user:
   - host: your ClickHouse Cloud host
   - port: `8443`
   - database: `bot_analytics`
   - SSL: enabled

Build four dashboard pages in Metabase using these views:

- `dashboard_summary_24h`
- `dashboard_trending_claims_24h`
- `dashboard_claim_group_spread_24h`
- `dashboard_high_risk_scams_24h`

Recommended layout:

- `Overview`: candidate volume, fact-check volume, reply volume, spend, high-risk claims
- `Trending Claims`: canonical claim, verdict, risk, groups reached, reply coverage
- `Scam / Risk Watch`: high-risk scam claims and countermessage-ready rows
- `Group Spread`: which groups are propagating each claim

Keep Metabase internal-only. Put it behind an authenticated reverse proxy, SSO, VPN, or network restriction before sharing it with any external stakeholders.

## Optional Webhook Mode

If you want webhook delivery instead of polling:

1. Set `TELEGRAM_INGEST_MODE=webhook` in `.env`.
2. Expose the API on a public HTTPS URL, set `PUBLIC_WEBHOOK_URL` in `.env`, then register the webhook:

```bash
./scripts/set_telegram_webhook.sh
```

3. For local development with a Cloudflare quick tunnel and automatic webhook registration, start the optional tunnel profile:

```bash
docker compose --profile tunnel up --build -d --remove-orphans
```

## Admin API

All `/admin/*` routes require:

```http
Authorization: Bearer <ADMIN_API_TOKEN>
```

The main admin endpoints now include:

- `GET /admin/groups/{group_external_id}/metrics`
- `POST /admin/outbreaks/refresh`
- `GET /admin/dashboard/summary`
- `GET /admin/dashboard/trending-claims`
- `GET /admin/dashboard/claims/{claim_key}/groups`

## Notes

- The app is designed to run without ClickHouse in local development. Analytics failures must never block replies.
- Raw inbound text, image bytes, audio bytes, and transcripts are processed in memory and not persisted.
- Polling is the default local-development ingress path because it does not need a public HTTPS endpoint.
- Polling mode and tunnel/webhook mode are mutually exclusive; stop `cloudflared` and `webhook-sync` before polling to prevent webhook re-registration and Telegram `409 Conflict` on `getUpdates`.
- The poller disables any existing Telegram webhook on startup before calling `getUpdates`.
- Webhook mode still requires a public HTTPS endpoint; local-only `localhost` webhooks will not work.
- Manual fact-checking supports both `/factcheck <claim>` and replying to a message with `@<bot_username>` to trigger a check of the replied message.
- Users can continue the thread by replying to a bot fact-check message with follow-up questions; the bot answers in-thread using Telegram reply mode.
- Bot replies include inline translation buttons. Users can translate each bot message once per target language (English, Chinese, Malay, Tamil), and duplicate translation requests for the same message/language are blocked.
- ClickHouse schemas and materialized views live in `sql/clickhouse_bot_analytics.sql`.
- The worker now refreshes outbreak hot claims on a schedule; ClickHouse outages should degrade to “no outbreak refresh,” not “no bot.”
- The optional `cloudflared` Compose profile is only there to expose `api:8000` to Telegram during local development when `TELEGRAM_INGEST_MODE=webhook`.
- The optional `webhook-sync` Compose service watches the Cloudflare quick-tunnel URL and re-registers the Telegram webhook automatically after tunnel restarts.
- `./scripts/set_telegram_webhook.sh` registers the bot against `${PUBLIC_WEBHOOK_URL}/webhooks/telegram`.
- `./scripts/get_cloudflare_tunnel_url.sh` prints the current Cloudflare quick-tunnel URL from Docker logs.
- `./scripts/debug_telegram.sh` calls `getMe` and `getWebhookInfo` for the configured bot token.
