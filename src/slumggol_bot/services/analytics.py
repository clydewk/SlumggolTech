from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable, Sequence
from urllib.parse import urlparse

import clickhouse_connect

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import (
    Actionability,
    AnalyticsEvent,
    ClaimCategory,
    ClaimGroupSpreadRow,
    DashboardSummary,
    GroupMetrics,
    HotClaim,
    RiskLevel,
    TrendingClaimRow,
    Verdict,
)


class AnalyticsSink:
    async def write(self, events: Iterable[AnalyticsEvent]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class NoopAnalyticsSink(AnalyticsSink):
    async def write(self, events: Iterable[AnalyticsEvent]) -> None:  # noqa: ARG002
        return None


class ClickHouseAnalyticsSink(AnalyticsSink):
    def __init__(self, settings: AppSettings) -> None:
        if not settings.clickhouse_url:
            raise ValueError("CLICKHOUSE_URL is required when ClickHouse is enabled.")
        parsed = urlparse(settings.clickhouse_url)
        self.client = clickhouse_connect.get_client(
            host=parsed.hostname or "",
            port=parsed.port or 8443,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            secure=parsed.scheme == "https",
            interface="https" if parsed.scheme == "https" else "http",
        )
        self.settings = settings

    async def write(self, events: Iterable[AnalyticsEvent]) -> None:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            buckets[event.table].append(event.payload)

        for table, rows in buckets.items():
            if not rows:
                continue
            columns = list(rows[0].keys())
            data = [[row.get(column) for column in columns] for row in rows]
            await asyncio.to_thread(
                self.client.insert,
                table,
                data,
                column_names=columns,
                settings={
                    "async_insert": self.settings.clickhouse_async_insert,
                    "wait_for_async_insert": self.settings.clickhouse_wait_for_async_insert,
                },
            )


class FailOpenAnalyticsSink(AnalyticsSink):
    def __init__(self, inner: AnalyticsSink) -> None:
        self.inner = inner

    async def write(self, events: Iterable[AnalyticsEvent]) -> None:
        try:
            await self.inner.write(events)
        except Exception:
            return None


class AnalyticsQueryService:
    async def get_group_metrics(
        self,
        group_id: str,
        window_hours: int,
    ) -> GroupMetrics:  # pragma: no cover
        raise NotImplementedError

    async def list_hot_claims(
        self,
        *,
        lookback_minutes: int,
        min_group_count: int,
        limit: int = 50,
    ) -> list[HotClaim]:  # pragma: no cover
        raise NotImplementedError

    async def get_dashboard_summary(
        self,
        window_hours: int,
    ) -> DashboardSummary:  # pragma: no cover
        raise NotImplementedError

    async def list_trending_claims(
        self,
        *,
        lookback_hours: int,
        min_group_count: int,
        limit: int = 20,
        category: ClaimCategory | None = None,
        risk_level: RiskLevel | None = None,
    ) -> list[TrendingClaimRow]:  # pragma: no cover
        raise NotImplementedError

    async def list_claim_group_spread(
        self,
        *,
        claim_key: str,
        lookback_hours: int,
    ) -> list[ClaimGroupSpreadRow]:  # pragma: no cover
        raise NotImplementedError


class NoopAnalyticsQueryService(AnalyticsQueryService):
    async def get_group_metrics(self, group_id: str, window_hours: int) -> GroupMetrics:  # noqa: ARG002
        return GroupMetrics(group_id=group_id)

    async def list_hot_claims(
        self,
        *,
        lookback_minutes: int,  # noqa: ARG002
        min_group_count: int,  # noqa: ARG002
        limit: int = 50,  # noqa: ARG002
    ) -> list[HotClaim]:
        return []

    async def get_dashboard_summary(self, window_hours: int) -> DashboardSummary:  # noqa: ARG002
        return DashboardSummary(lookback_hours=window_hours)

    async def list_trending_claims(
        self,
        *,
        lookback_hours: int,  # noqa: ARG002
        min_group_count: int,  # noqa: ARG002
        limit: int = 20,  # noqa: ARG002
        category: ClaimCategory | None = None,  # noqa: ARG002
        risk_level: RiskLevel | None = None,  # noqa: ARG002
    ) -> list[TrendingClaimRow]:
        return []

    async def list_claim_group_spread(
        self,
        *,
        claim_key: str,  # noqa: ARG002
        lookback_hours: int,  # noqa: ARG002
    ) -> list[ClaimGroupSpreadRow]:
        return []


class ClickHouseAnalyticsQueryService(AnalyticsQueryService):
    def __init__(self, sink: ClickHouseAnalyticsSink) -> None:
        self.client = sink.client

    async def get_group_metrics(self, group_id: str, window_hours: int) -> GroupMetrics:
        def _query() -> GroupMetrics:
            hash_reuse = self.client.query(
                """
                SELECT countDistinct(hash_key)
                FROM hash_reuse_1h
                WHERE window_start >= now() - INTERVAL %(hours)s HOUR
                  AND group_id = %(group_id)s
                """,
                parameters={"hours": window_hours, "group_id": group_id},
            ).first_item or 0
            claim_spread = self.client.query(
                """
                SELECT countDistinct(claim_key)
                FROM claim_spread_5m
                WHERE window_start >= now() - INTERVAL %(hours)s HOUR
                  AND group_id = %(group_id)s
                """,
                parameters={"hours": window_hours, "group_id": group_id},
            ).first_item or 0
            spend = self.client.query(
                """
                SELECT sum(total_cost_usd)
                FROM model_spend_daily
                WHERE day >= toDate(now() - INTERVAL %(hours)s HOUR)
                  AND group_id = %(group_id)s
                """,
                parameters={"hours": window_hours, "group_id": group_id},
            ).first_item or 0
            replies = self.client.query(
                """
                SELECT sum(reply_count)
                FROM reply_outcomes_daily
                WHERE day >= toDate(now() - INTERVAL %(hours)s HOUR)
                  AND group_id = %(group_id)s
                """,
                parameters={"hours": window_hours, "group_id": group_id},
            ).first_item or 0
            return GroupMetrics(
                group_id=group_id,
                hash_reuse_count=_coerce_int(hash_reuse),
                claim_spread_count=_coerce_int(claim_spread),
                spend_usd=_coerce_float(spend),
                reply_count=_coerce_int(replies),
            )

        return await asyncio.to_thread(_query)

    async def list_hot_claims(
        self,
        *,
        lookback_minutes: int,
        min_group_count: int,
        limit: int = 50,
    ) -> list[HotClaim]:
        def _query() -> list[HotClaim]:
            results = self.client.query(
                """
                SELECT claim_key, countDistinct(group_id) AS score
                FROM claim_spread_5m
                WHERE window_start >= now() - INTERVAL %(lookback)s MINUTE
                GROUP BY claim_key
                HAVING countDistinct(group_id) >= %(min_groups)s
                ORDER BY score DESC
                LIMIT %(limit)s
                """,
                parameters={
                    "lookback": lookback_minutes,
                    "min_groups": min_group_count,
                    "limit": limit,
                },
            )
            return [
                HotClaim(
                    hash_key=row[0],
                    claim_key=row[0],
                    reason="claim_spread",
                    score=float(row[1]),
                )
                for row in results.result_rows
            ]

        return await asyncio.to_thread(_query)

    async def get_dashboard_summary(self, window_hours: int) -> DashboardSummary:
        def _query() -> DashboardSummary:
            result = self.client.query(
                """
                SELECT
                    (
                        SELECT count()
                        FROM message_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                          AND candidate = 1
                    ) AS candidate_message_count,
                    (
                        SELECT count()
                        FROM claim_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                    ) AS factcheck_count,
                    (
                        SELECT sum(reply_count)
                        FROM reply_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                    ) AS reply_count,
                    (
                        SELECT countDistinct(group_id)
                        FROM message_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                    ) AS unique_groups,
                    (
                        SELECT countDistinct(claim_key)
                        FROM claim_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                          AND claim_key IS NOT NULL
                          AND claim_key != ''
                    ) AS trending_claim_count,
                    (
                        SELECT countDistinct(claim_key)
                        FROM claim_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                          AND claim_key IS NOT NULL
                          AND claim_key != ''
                          AND risk_level = 'high'
                    ) AS high_risk_claim_count,
                    (
                        SELECT sum(estimated_cost_usd + transcription_cost_usd)
                        FROM usage_events
                        WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                    ) AS spend_usd
                """,
                parameters={"hours": window_hours},
            )
            row = result.result_rows[0] if result.result_rows else ()
            return DashboardSummary(
                lookback_hours=window_hours,
                candidate_message_count=_coerce_int(_first_or_zero(row, 0)),
                factcheck_count=_coerce_int(_first_or_zero(row, 1)),
                reply_count=_coerce_int(_first_or_zero(row, 2)),
                unique_groups=_coerce_int(_first_or_zero(row, 3)),
                trending_claim_count=_coerce_int(_first_or_zero(row, 4)),
                high_risk_claim_count=_coerce_int(_first_or_zero(row, 5)),
                spend_usd=_coerce_float(_first_or_zero(row, 6)),
            )

        return await asyncio.to_thread(_query)

    async def list_trending_claims(
        self,
        *,
        lookback_hours: int,
        min_group_count: int,
        limit: int = 20,
        category: ClaimCategory | None = None,
        risk_level: RiskLevel | None = None,
    ) -> list[TrendingClaimRow]:
        def _query() -> list[TrendingClaimRow]:
            results = self.client.query(
                """
                SELECT
                    claims.claim_key,
                    claims.canonical_claim_en,
                    claims.claim_category,
                    claims.risk_level,
                    claims.actionability,
                    factchecks.latest_verdict,
                    factchecks.has_official_sg_source,
                    factchecks.official_source_domain_count,
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
                    FROM claim_events
                    WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                      AND claim_key IS NOT NULL
                      AND claim_key != ''
                      AND (%(category)s = '' OR claim_category = %(category)s)
                      AND (%(risk_level)s = '' OR risk_level = %(risk_level)s)
                    GROUP BY claim_key
                    HAVING countDistinct(group_id) >= %(min_groups)s
                ) AS claims
                LEFT JOIN
                (
                    SELECT
                        claim_key,
                        argMax(verdict, occurred_at) AS latest_verdict,
                        max(has_official_sg_source) AS has_official_sg_source,
                        max(official_source_domain_count) AS official_source_domain_count
                    FROM factcheck_events
                    WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                      AND claim_key IS NOT NULL
                      AND claim_key != ''
                    GROUP BY claim_key
                ) AS factchecks USING (claim_key)
                LEFT JOIN
                (
                    SELECT claim_key, sum(reply_count) AS reply_count
                    FROM reply_events
                    WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                      AND claim_key IS NOT NULL
                      AND claim_key != ''
                    GROUP BY claim_key
                ) AS replies USING (claim_key)
                ORDER BY
                    claims.distinct_groups DESC,
                    claims.event_count DESC,
                    claims.last_seen_at DESC
                LIMIT %(limit)s
                """,
                parameters={
                    "hours": lookback_hours,
                    "category": category.value if category else "",
                    "risk_level": risk_level.value if risk_level else "",
                    "min_groups": min_group_count,
                    "limit": limit,
                },
            )
            return [
                TrendingClaimRow(
                    claim_key=str(row[0]),
                    canonical_claim_en=str(row[1] or ""),
                    claim_category=_coerce_claim_category(row[2]),
                    risk_level=_coerce_risk_level(row[3]),
                    actionability=_coerce_actionability(row[4]),
                    latest_verdict=_coerce_verdict(row[5]),
                    has_official_sg_source=bool(_coerce_int(row[6])),
                    official_source_domain_count=_coerce_int(row[7]),
                    distinct_groups=_coerce_int(row[8]),
                    event_count=_coerce_int(row[9]),
                    reply_count=_coerce_int(row[10]),
                    max_confidence=_coerce_float(row[11]),
                    first_seen_at=row[12],
                    last_seen_at=row[13],
                )
                for row in results.result_rows
            ]

        return await asyncio.to_thread(_query)

    async def list_claim_group_spread(
        self,
        *,
        claim_key: str,
        lookback_hours: int,
    ) -> list[ClaimGroupSpreadRow]:
        def _query() -> list[ClaimGroupSpreadRow]:
            results = self.client.query(
                """
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
                    FROM claim_events
                    WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                      AND claim_key = %(claim_key)s
                    GROUP BY claim_key, group_id
                ) AS claims
                LEFT JOIN
                (
                    SELECT claim_key, group_id, sum(reply_count) AS reply_count
                    FROM reply_events
                    WHERE occurred_at >= now() - INTERVAL %(hours)s HOUR
                      AND claim_key = %(claim_key)s
                    GROUP BY claim_key, group_id
                ) AS replies
                  ON claims.claim_key = replies.claim_key
                 AND claims.group_id = replies.group_id
                ORDER BY claims.event_count DESC, claims.last_seen_at DESC
                """,
                parameters={"hours": lookback_hours, "claim_key": claim_key},
            )
            return [
                ClaimGroupSpreadRow(
                    claim_key=str(row[0]),
                    group_id=str(row[1]),
                    group_display_name=str(row[2]) if row[2] else None,
                    first_seen_at=row[3],
                    last_seen_at=row[4],
                    event_count=_coerce_int(row[5]),
                    reply_count=_coerce_int(row[6]),
                )
                for row in results.result_rows
            ]

        return await asyncio.to_thread(_query)


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        return int(value)
    return 0


def _coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return 0.0


def _coerce_claim_category(value: object) -> ClaimCategory:
    if isinstance(value, ClaimCategory):
        return value
    if isinstance(value, str):
        try:
            return ClaimCategory(value)
        except ValueError:
            return ClaimCategory.OTHER
    return ClaimCategory.OTHER


def _coerce_risk_level(value: object) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    if isinstance(value, str):
        try:
            return RiskLevel(value)
        except ValueError:
            return RiskLevel.LOW
    return RiskLevel.LOW


def _coerce_actionability(value: object) -> Actionability:
    if isinstance(value, Actionability):
        return value
    if isinstance(value, str):
        try:
            return Actionability(value)
        except ValueError:
            return Actionability.MONITOR
    return Actionability.MONITOR


def _coerce_verdict(value: object) -> Verdict | None:
    if isinstance(value, Verdict):
        return value
    if isinstance(value, str) and value:
        try:
            return Verdict(value)
        except ValueError:
            return None
    return None


def _first_or_zero(row: Sequence[object], index: int) -> object:
    if index >= len(row):
        return 0
    return row[index]
