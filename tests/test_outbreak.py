from __future__ import annotations

from slumggol_bot.schemas import HotClaim
from slumggol_bot.services.outbreak import OutbreakService


class FakeQueryService:
    async def list_hot_claims(
        self,
        *,
        lookback_minutes: int,
        min_group_count: int,
        limit: int = 50,
    ) -> list[HotClaim]:  # noqa: ARG002
        return [
            HotClaim(
                hash_key="claim-key-1",
                claim_key="claim-key-1",
                reason="spread",
                score=4.0,
            )
        ]


class FakeHotStore:
    def __init__(self) -> None:
        self.replaced: list[HotClaim] = []

    async def replace(self, claims, ttl_seconds: int):  # noqa: ANN001
        self.replaced = list(claims)


class FakeHotRepository:
    def __init__(self) -> None:
        self.replaced: list[HotClaim] = []

    async def replace_active(self, claims, expires_at):  # noqa: ANN001
        self.replaced = list(claims)


class FakeClaimCacheRepository:
    async def text_simhashes_for_claim_keys(self, claim_keys: list[str]) -> dict[str, str]:
        return {claim_key: f"simhash-{claim_key}" for claim_key in claim_keys}


async def test_outbreak_service_prewarms_store_and_repository() -> None:
    hot_store = FakeHotStore()
    repository = FakeHotRepository()
    service = OutbreakService(
        query_service=FakeQueryService(),
        hot_claim_store=hot_store,
        hot_claim_repository=repository,
        claim_cache_repository=FakeClaimCacheRepository(),
        lookback_minutes=60,
        min_group_count=2,
    )
    claims = await service.refresh_hot_claims()
    assert len(claims) == 1
    assert hot_store.replaced[0].claim_key == "claim-key-1"
    assert repository.replaced[0].hash_key == "claim-key-1"
    assert repository.replaced[0].text_simhash == "simhash-claim-key-1"
