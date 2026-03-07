from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from slumggol_bot.db.repositories import HotClaimRepository
from slumggol_bot.schemas import HotClaim
from slumggol_bot.services.analytics import AnalyticsQueryService
from slumggol_bot.services.cache import HotClaimStore


class ClaimCacheLookup(Protocol):
    async def text_simhashes_for_claim_keys(self, claim_keys: list[str]) -> dict[str, str]: ...


class OutbreakService:
    def __init__(
        self,
        *,
        query_service: AnalyticsQueryService,
        hot_claim_store: HotClaimStore,
        hot_claim_repository: HotClaimRepository,
        claim_cache_repository: ClaimCacheLookup,
        lookback_minutes: int,
        min_group_count: int,
    ) -> None:
        self.query_service = query_service
        self.hot_claim_store = hot_claim_store
        self.hot_claim_repository = hot_claim_repository
        self.claim_cache_repository = claim_cache_repository
        self.lookback_minutes = lookback_minutes
        self.min_group_count = min_group_count

    async def refresh_hot_claims(self) -> list[HotClaim]:
        claims = await self.query_service.list_hot_claims(
            lookback_minutes=self.lookback_minutes,
            min_group_count=self.min_group_count,
        )
        simhashes = await self.claim_cache_repository.text_simhashes_for_claim_keys(
            [claim.claim_key for claim in claims if claim.claim_key]
        )
        claims = [
            claim.model_copy(update={"text_simhash": simhashes.get(claim.claim_key or "")})
            for claim in claims
        ]
        ttl_seconds = self.lookback_minutes * 60
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        await self.hot_claim_store.replace(claims, ttl_seconds=ttl_seconds)
        await self.hot_claim_repository.replace_active(claims, expires_at=expires_at)
        return claims
