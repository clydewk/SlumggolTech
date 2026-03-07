from __future__ import annotations

from datetime import datetime, timedelta, timezone

from slumggol_bot.db.repositories import HotClaimRepository
from slumggol_bot.schemas import HotClaim
from slumggol_bot.services.analytics import AnalyticsQueryService
from slumggol_bot.services.cache import HotClaimStore


class OutbreakService:
    def __init__(
        self,
        *,
        query_service: AnalyticsQueryService,
        hot_claim_store: HotClaimStore,
        hot_claim_repository: HotClaimRepository,
        lookback_minutes: int,
        min_group_count: int,
    ) -> None:
        self.query_service = query_service
        self.hot_claim_store = hot_claim_store
        self.hot_claim_repository = hot_claim_repository
        self.lookback_minutes = lookback_minutes
        self.min_group_count = min_group_count

    async def refresh_hot_claims(self) -> list[HotClaim]:
        claims = await self.query_service.list_hot_claims(
            lookback_minutes=self.lookback_minutes,
            min_group_count=self.min_group_count,
        )
        ttl_seconds = self.lookback_minutes * 60
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        await self.hot_claim_store.replace(claims, ttl_seconds=ttl_seconds)
        await self.hot_claim_repository.replace_active(claims, expires_at=expires_at)
        return claims

