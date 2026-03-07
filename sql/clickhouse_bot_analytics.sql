CREATE DATABASE IF NOT EXISTS bot_analytics;

CREATE TABLE IF NOT EXISTS bot_analytics.message_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
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
    message_id String,
    claim_key Nullable(String),
    canonical_claim_en String,
    reply_language String,
    confidence Float64
)
ENGINE = MergeTree
PARTITION BY toDate(occurred_at)
ORDER BY (occurred_at, group_id, message_id)
TTL occurred_at + INTERVAL 30 DAY DELETE;

CREATE TABLE IF NOT EXISTS bot_analytics.factcheck_events (
    event_id String,
    occurred_at DateTime64(3, 'UTC'),
    group_id String,
    message_id String,
    claim_key Nullable(String),
    verdict LowCardinality(String),
    confidence Float64,
    cache_hit UInt8,
    cache_match_type LowCardinality(String),
    cache_match_distance UInt8,
    needs_reply UInt8,
    reason_codes Array(String),
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
    message_id String,
    claim_key Nullable(String),
    reply_language String,
    confidence Float64,
    verdict LowCardinality(String),
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
    message_id String,
    claim_key Nullable(String),
    model LowCardinality(String),
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

CREATE TABLE IF NOT EXISTS bot_analytics.reply_outcomes_daily (
    day Date,
    group_id String,
    verdict LowCardinality(String),
    reply_count UInt64,
    avg_confidence Float64
)
ENGINE = ReplacingMergeTree
ORDER BY (day, group_id, verdict)
TTL day + INTERVAL 2 YEAR DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS bot_analytics.reply_outcomes_daily_mv
TO bot_analytics.reply_outcomes_daily AS
SELECT
    toDate(occurred_at) AS day,
    group_id,
    verdict,
    sum(reply_count) AS reply_count,
    avg(confidence) AS avg_confidence
FROM bot_analytics.reply_events
GROUP BY day, group_id, verdict;
