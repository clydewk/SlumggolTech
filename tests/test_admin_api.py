from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from slumggol_bot.api.app import app, get_settings
from slumggol_bot.schemas import (
    Actionability,
    ClaimCategory,
    ClaimGroupSpreadRow,
    DashboardSummary,
    GroupMetrics,
    RiskLevel,
    TrendingClaimRow,
    Verdict,
)


class FakeAnalyticsQueryService:
    async def get_group_metrics(self, group_id: str, window_hours: int) -> GroupMetrics:
        return GroupMetrics(group_id=group_id, hash_reuse_count=1, claim_spread_count=2)

    async def list_hot_claims(
        self,
        *,
        lookback_minutes: int,
        min_group_count: int,
        limit: int = 50,
    ):
        return []

    async def get_dashboard_summary(self, window_hours: int) -> DashboardSummary:
        return DashboardSummary(
            lookback_hours=window_hours,
            candidate_message_count=4,
            factcheck_count=3,
            reply_count=2,
            unique_groups=2,
            trending_claim_count=2,
            high_risk_claim_count=1,
            spend_usd=1.23,
        )

    async def list_trending_claims(
        self,
        *,
        lookback_hours: int,
        min_group_count: int,
        limit: int = 20,
        category: ClaimCategory | None = None,
        risk_level: RiskLevel | None = None,
    ) -> list[TrendingClaimRow]:
        return [
            TrendingClaimRow(
                claim_key="claim-key-1",
                canonical_claim_en="canonical claim",
                claim_category=category or ClaimCategory.SCAM,
                risk_level=risk_level or RiskLevel.HIGH,
                actionability=Actionability.COUNTERMESSAGE_READY,
                latest_verdict=Verdict.FALSE,
                has_official_sg_source=True,
                official_source_domain_count=2,
                distinct_groups=min_group_count,
                event_count=5,
                reply_count=2,
                max_confidence=0.94,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        ]

    async def list_claim_group_spread(
        self,
        *,
        claim_key: str,
        lookback_hours: int,
    ) -> list[ClaimGroupSpreadRow]:
        return [
            ClaimGroupSpreadRow(
                claim_key=claim_key,
                group_id="group-1",
                group_display_name="Group One",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                event_count=3,
                reply_count=1,
            )
        ]


def test_admin_dashboard_requires_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret-token")
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/admin/dashboard/summary")

    assert response.status_code == 401


def test_admin_dashboard_endpoints_return_query_results(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret-token")
    get_settings.cache_clear()
    original_query_service = app.state.analytics_query_service
    app.state.analytics_query_service = FakeAnalyticsQueryService()
    client = TestClient(app)

    try:
        headers = {"Authorization": "Bearer secret-token"}

        summary = client.get("/admin/dashboard/summary?hours=48", headers=headers)
        trending = client.get(
            "/admin/dashboard/trending-claims?hours=12&min_group_count=3&category=scam&risk_level=high",
            headers=headers,
        )
        group_metrics = client.get("/admin/groups/group-1/metrics?hours=12", headers=headers)
        spread = client.get("/admin/dashboard/claims/claim-key-1/groups?hours=12", headers=headers)

        assert summary.status_code == 200
        assert summary.json()["lookback_hours"] == 48
        assert summary.json()["candidate_message_count"] == 4

        assert trending.status_code == 200
        assert trending.json()[0]["claim_category"] == "scam"
        assert trending.json()[0]["risk_level"] == "high"
        assert trending.json()[0]["actionability"] == "countermessage_ready"

        assert group_metrics.status_code == 200
        assert group_metrics.json()["claim_spread_count"] == 2

        assert spread.status_code == 200
        assert spread.json()[0]["group_display_name"] == "Group One"
    finally:
        app.state.analytics_query_service = original_query_service
        get_settings.cache_clear()


def test_admin_dashboard_rejects_invalid_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret-token")
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get(
        "/admin/dashboard/summary",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
