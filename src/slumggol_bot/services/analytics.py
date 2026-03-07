from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import clickhouse_connect

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import AnalyticsEvent, GroupMetrics, HotClaim


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
    async def get_group_metrics(self, group_id: str, window_hours: int) -> GroupMetrics:  # pragma: no cover
        raise NotImplementedError

    async def list_hot_claims(
        self,
        *,
        lookback_minutes: int,
        min_group_count: int,
        limit: int = 50,
    ) -> list[HotClaim]:  # pragma: no cover
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
                hash_reuse_count=int(hash_reuse),
                claim_spread_count=int(claim_spread),
                spend_usd=float(spend or 0),
                reply_count=int(replies or 0),
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
            hot_claims: list[HotClaim] = []
            for row in results.result_rows:
                claim_key = row[0]
                score = float(row[1])
                hot_claims.append(
                    HotClaim(
                        hash_key=claim_key,
                        claim_key=claim_key,
                        reason="claim_spread",
                        score=score,
                    )
                )
            return hot_claims

        return await asyncio.to_thread(_query)
