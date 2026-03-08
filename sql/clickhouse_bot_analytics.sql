CREATE DATABASE IF NOT EXISTS bot_analytics;

CREATE TABLE IF NOT EXISTS bot_analytics.message_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
    group_display_name String,
    sender_hash String,
    message_id String,
    forwarded UInt8,
    forwarded_many_times UInt8,
    content_kind LowCardinality(String),
    text_sha256 Nullable(String),
    text_simhash Nullable(String),
    media_sha256 Nullable(String),
    image_phash Nullable(String),
    transcript_sha256 Nullable(String),
    language_code String,
    candidate UInt8,
    reason_codes Array(String),
    heuristic_match_type LowCardinality(String),
    heuristic_match_distance UInt8
)
ENGINE = MergeTree
PARTITION BY toDate(occurred_at)
ORDER BY (occurred_at, group_id, message_id)
TTL occurred_at + INTERVAL 30 DAY DELETE;

CREATE TABLE IF NOT EXISTS bot_analytics.claim_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
    group_display_name String,
    message_id String,
    claim_key Nullable(String),
    canonical_claim_en String,
    reply_language String,
    confidence Float64,
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    has_official_sg_source UInt8,
    official_source_domain_count UInt64
)
ENGINE = MergeTree
PARTITION BY toDate(occurred_at)
ORDER BY (occurred_at, group_id, message_id)
TTL occurred_at + INTERVAL 30 DAY DELETE;

CREATE TABLE IF NOT EXISTS bot_analytics.factcheck_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
    group_display_name String,
    message_id String,
    claim_key Nullable(String),
    verdict LowCardinality(String),
    confidence Float64,
    cache_hit UInt8,
    cache_match_type LowCardinality(String),
    cache_match_distance UInt8,
    needs_reply UInt8,
    reason_codes Array(String),
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    has_official_sg_source UInt8,
    official_source_domain_count UInt64,
    source_domains Array(String)
)
ENGINE = MergeTree
PARTITION BY toDate(occurred_at)
ORDER BY (occurred_at, group_id, message_id)
TTL occurred_at + INTERVAL 30 DAY DELETE;

CREATE TABLE IF NOT EXISTS bot_analytics.reply_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
    group_display_name String,
    message_id String,
    claim_key Nullable(String),
    reply_language String,
    confidence Float64,
    verdict LowCardinality(String),
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    has_official_sg_source UInt8,
    official_source_domain_count UInt64,
    reply_count UInt64
)
ENGINE = MergeTree
PARTITION BY toDate(occurred_at)
ORDER BY (occurred_at, group_id, message_id)
TTL occurred_at + INTERVAL 30 DAY DELETE;

CREATE TABLE IF NOT EXISTS bot_analytics.usage_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
    group_display_name String,
    message_id String,
    claim_key Nullable(String),
    model LowCardinality(String),
    auxiliary_model Nullable(String),
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    input_tokens UInt64,
    output_tokens UInt64,
    reasoning_tokens UInt64,
    web_search_calls UInt64,
    estimated_cost_usd Float64,
    transcription_cost_usd Float64
)
ENGINE = MergeTree
PARTITION BY toDate(occurred_at)
ORDER BY (occurred_at, group_id, message_id)
TTL occurred_at + INTERVAL 30 DAY DELETE;

CREATE TABLE IF NOT EXISTS bot_analytics.hash_reuse_1h (
    window_start DateTime('UTC'),
    hash_key String,
    hash_type LowCardinality(String),
    group_id String,
    event_count UInt64
)
ENGINE = SummingMergeTree
PARTITION BY toDate(window_start)
ORDER BY (window_start, hash_type, hash_key, group_id)
TTL window_start + INTERVAL 180 DAY DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.hash_reuse_1h_mv
TO bot_analytics.hash_reuse_1h AS
WITH coalesce(
    nullIf(text_sha256, ''),
    nullIf(media_sha256, ''),
    nullIf(image_phash, ''),
    nullIf(transcript_sha256, '')
) AS hash_key
SELECT
    toStartOfHour(occurred_at) AS window_start,
    hash_key,
    multiIf(
        notEmpty(ifNull(text_sha256, '')), 'text',
        notEmpty(ifNull(media_sha256, '')), 'media',
        notEmpty(ifNull(image_phash, '')), 'image',
        'transcript'
    ) AS hash_type,
    group_id,
    toUInt64(count()) AS event_count
FROM bot_analytics.message_events
WHERE hash_key IS NOT NULL
GROUP BY window_start, hash_key, hash_type, group_id;

CREATE TABLE IF NOT EXISTS bot_analytics.claim_spread_5m (
    window_start DateTime('UTC'),
    claim_key String,
    group_id String,
    reply_language String,
    event_count UInt64
)
ENGINE = SummingMergeTree
PARTITION BY toDate(window_start)
ORDER BY (window_start, claim_key, group_id)
TTL window_start + INTERVAL 180 DAY DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.claim_spread_5m_mv
TO bot_analytics.claim_spread_5m AS
SELECT
    toStartOfFiveMinutes(occurred_at) AS window_start,
    claim_key,
    group_id,
    reply_language,
    toUInt64(count()) AS event_count
FROM bot_analytics.claim_events
WHERE claim_key IS NOT NULL AND claim_key != ''
GROUP BY window_start, claim_key, group_id, reply_language;

CREATE TABLE IF NOT EXISTS bot_analytics.claim_intel_5m (
    window_start DateTime('UTC'),
    claim_key String,
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    group_id String,
    event_count UInt64
)
ENGINE = SummingMergeTree
PARTITION BY toDate(window_start)
ORDER BY (window_start, claim_category, risk_level, actionability, claim_key, group_id)
TTL window_start + INTERVAL 180 DAY DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.claim_intel_5m_mv
TO bot_analytics.claim_intel_5m AS
SELECT
    toStartOfFiveMinutes(occurred_at) AS window_start,
    claim_key,
    claim_category,
    risk_level,
    actionability,
    group_id,
    toUInt64(count()) AS event_count
FROM bot_analytics.claim_events
WHERE claim_key IS NOT NULL AND claim_key != ''
GROUP BY window_start, claim_key, claim_category, risk_level, actionability, group_id;

CREATE TABLE IF NOT EXISTS bot_analytics.model_spend_daily (
    day Date,
    group_id String,
    model LowCardinality(String),
    total_cost_usd Float64,
    total_input_tokens UInt64,
    total_output_tokens UInt64,
    total_reasoning_tokens UInt64,
    total_web_search_calls UInt64
)
ENGINE = SummingMergeTree
ORDER BY (day, group_id, model)
TTL day + INTERVAL 2 YEAR DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.model_spend_daily_mv
TO bot_analytics.model_spend_daily AS
SELECT
    toDate(occurred_at) AS day,
    group_id,
    model,
    sum(estimated_cost_usd + transcription_cost_usd) AS total_cost_usd,
    sum(input_tokens) AS total_input_tokens,
    sum(output_tokens) AS total_output_tokens,
    sum(reasoning_tokens) AS total_reasoning_tokens,
    sum(web_search_calls) AS total_web_search_calls
FROM bot_analytics.usage_events
GROUP BY day, group_id, model;

CREATE TABLE IF NOT EXISTS bot_analytics.source_quality_daily (
    day Date,
    group_id String,
    verdict LowCardinality(String),
    source_hit_count UInt64,
    unsupported_count UInt64,
    contradiction_count UInt64
)
ENGINE = SummingMergeTree
ORDER BY (day, group_id, verdict)
TTL day + INTERVAL 2 YEAR DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.source_quality_daily_mv
TO bot_analytics.source_quality_daily AS
SELECT
    toDate(occurred_at) AS day,
    group_id,
    verdict,
    sum(length(source_domains)) AS source_hit_count,
    sum(if(verdict = 'unsupported', 1, 0)) AS unsupported_count,
    sum(if(verdict = 'misleading', 1, 0)) AS contradiction_count
FROM bot_analytics.factcheck_events
GROUP BY day, group_id, verdict;

CREATE TABLE IF NOT EXISTS bot_analytics.factcheck_intel_daily (
    day Date,
    claim_key String,
    verdict LowCardinality(String),
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    has_official_sg_source UInt64,
    official_source_domain_count UInt64,
    event_count UInt64
)
ENGINE = SummingMergeTree
ORDER BY (day, claim_category, risk_level, actionability, verdict, claim_key, has_official_sg_source)
TTL day + INTERVAL 2 YEAR DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.factcheck_intel_daily_mv
TO bot_analytics.factcheck_intel_daily AS
SELECT
    toDate(occurred_at) AS day,
    ifNull(claim_key, '') AS claim_key,
    verdict,
    claim_category,
    risk_level,
    actionability,
    sum(has_official_sg_source) AS has_official_sg_source,
    sum(official_source_domain_count) AS official_source_domain_count,
    toUInt64(count()) AS event_count
FROM bot_analytics.factcheck_events
WHERE claim_key IS NOT NULL AND claim_key != ''
GROUP BY day, claim_key, verdict, claim_category, risk_level, actionability;

CREATE TABLE IF NOT EXISTS bot_analytics.reply_outcomes_daily (
    day Date,
    group_id String,
    verdict LowCardinality(String),
    claim_category LowCardinality(String),
    risk_level LowCardinality(String),
    actionability LowCardinality(String),
    reply_count UInt64,
    confidence_sum Float64
)
ENGINE = SummingMergeTree
ORDER BY (day, group_id, verdict, claim_category, risk_level, actionability)
TTL day + INTERVAL 2 YEAR DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.reply_outcomes_daily_mv
TO bot_analytics.reply_outcomes_daily AS
SELECT
    toDate(occurred_at) AS day,
    group_id,
    verdict,
    claim_category,
    risk_level,
    actionability,
    sum(reply_count) AS reply_count,
    sum(confidence * bot_analytics.reply_events.reply_count) AS confidence_sum
FROM bot_analytics.reply_events
GROUP BY day, group_id, verdict, claim_category, risk_level, actionability;

CREATE VIEW IF NOT EXISTS bot_analytics.dashboard_summary_24h AS
SELECT
    24 AS lookback_hours,
    (
        SELECT count()
        FROM bot_analytics.message_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
          AND candidate = 1
    ) AS candidate_message_count,
    (
        SELECT count()
        FROM bot_analytics.claim_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
    ) AS factcheck_count,
    (
        SELECT sum(reply_count)
        FROM bot_analytics.reply_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
    ) AS reply_count,
    (
        SELECT countDistinct(group_id)
        FROM bot_analytics.message_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
    ) AS unique_groups,
    (
        SELECT countDistinct(claim_key)
        FROM bot_analytics.claim_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
          AND claim_key IS NOT NULL
          AND claim_key != ''
    ) AS trending_claim_count,
    (
        SELECT countDistinct(claim_key)
        FROM bot_analytics.claim_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
          AND claim_key IS NOT NULL
          AND claim_key != ''
          AND risk_level = 'high'
    ) AS high_risk_claim_count,
    (
        SELECT sum(estimated_cost_usd + transcription_cost_usd)
        FROM bot_analytics.usage_events
        WHERE occurred_at >= now() - INTERVAL 24 HOUR
    ) AS spend_usd;

CREATE VIEW IF NOT EXISTS bot_analytics.dashboard_trending_claims_24h AS
SELECT
    claims.claim_key,
    claims.canonical_claim_en,
    claims.claim_category,
    claims.risk_level,
    claims.actionability,
    factchecks.latest_verdict AS latest_verdict,
    factchecks.has_official_sg_source AS has_official_sg_source,
    factchecks.official_source_domain_count AS official_source_domain_count,
    claims.distinct_groups,
    claims.event_count,
    coalesce(replies.reply_count, 0) AS reply_count,
    claims.max_confidence,
    claims.first_seen_at,
    claims.last_seen_at
FROM
(
    SELECT
        claim_key,
        argMax(canonical_claim_en, occurred_at) AS canonical_claim_en,
        argMax(claim_category, occurred_at) AS claim_category,
        argMax(risk_level, occurred_at) AS risk_level,
        argMax(actionability, occurred_at) AS actionability,
        countDistinct(group_id) AS distinct_groups,
        count() AS event_count,
        max(confidence) AS max_confidence,
        min(occurred_at) AS first_seen_at,
        max(occurred_at) AS last_seen_at
    FROM bot_analytics.claim_events
    WHERE occurred_at >= now() - INTERVAL 24 HOUR
      AND claim_key IS NOT NULL
      AND claim_key != ''
    GROUP BY claim_key
) AS claims
LEFT JOIN
(
    SELECT
        claim_key,
        argMax(verdict, occurred_at) AS latest_verdict,
        max(has_official_sg_source) AS has_official_sg_source,
        max(official_source_domain_count) AS official_source_domain_count
    FROM bot_analytics.factcheck_events
    WHERE occurred_at >= now() - INTERVAL 24 HOUR
      AND claim_key IS NOT NULL
      AND claim_key != ''
    GROUP BY claim_key
) AS factchecks USING (claim_key)
LEFT JOIN
(
    SELECT claim_key, sum(reply_count) AS reply_count
    FROM bot_analytics.reply_events
    WHERE occurred_at >= now() - INTERVAL 24 HOUR
      AND claim_key IS NOT NULL
      AND claim_key != ''
    GROUP BY claim_key
) AS replies USING (claim_key);

CREATE VIEW IF NOT EXISTS bot_analytics.dashboard_claim_group_spread_24h AS
SELECT
    claims.claim_key,
    claims.group_id,
    claims.group_display_name,
    claims.first_seen_at,
    claims.last_seen_at,
    claims.event_count,
    coalesce(replies.reply_count, 0) AS reply_count
FROM
(
    SELECT
        claim_key,
        group_id,
        argMax(group_display_name, occurred_at) AS group_display_name,
        min(occurred_at) AS first_seen_at,
        max(occurred_at) AS last_seen_at,
        count() AS event_count
    FROM bot_analytics.claim_events
    WHERE occurred_at >= now() - INTERVAL 24 HOUR
      AND claim_key IS NOT NULL
      AND claim_key != ''
    GROUP BY claim_key, group_id
) AS claims
LEFT JOIN
(
    SELECT claim_key, group_id, sum(reply_count) AS reply_count
    FROM bot_analytics.reply_events
    WHERE occurred_at >= now() - INTERVAL 24 HOUR
      AND claim_key IS NOT NULL
      AND claim_key != ''
    GROUP BY claim_key, group_id
) AS replies
  ON claims.claim_key = replies.claim_key
 AND claims.group_id = replies.group_id;

CREATE VIEW IF NOT EXISTS bot_analytics.dashboard_high_risk_scams_24h AS
SELECT *
FROM bot_analytics.dashboard_trending_claims_24h
WHERE claim_category = 'scam'
  AND risk_level = 'high';
