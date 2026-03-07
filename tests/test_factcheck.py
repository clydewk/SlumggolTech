from __future__ import annotations

from datetime import datetime, timezone

import pytest

from slumggol_bot.schemas import (
    ContentKind,
    EvidenceSource,
    FactCheckResult,
    GroupStyleProfile,
    HotClaim,
    ModelUsage,
    NormalizedMessage,
    Verdict,
)
from slumggol_bot.services.cache import InMemoryHotClaimStore
from slumggol_bot.services.factcheck import FactCheckService
from slumggol_bot.services.style_profiles import StyleProfileService


class FakeCacheRepo:
    def __init__(self) -> None:
        self.storage = {}

    async def get(self, claim_key: str):
        return self.storage.get(claim_key)

    async def upsert(self, *, claim_key: str, result: FactCheckResult, expires_at):  # noqa: ANN001
        self.storage[claim_key] = type(
            "CachedEntry",
            (),
            {
                "verdict": result.verdict.value,
                "confidence": result.confidence,
                "reply_language": result.reply_language,
                "reply_template": result.reply_text,
                "evidence_json": [item.model_dump(mode="json") for item in result.evidence],
                "expires_at": expires_at,
            },
        )()


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, audio_url: str):  # noqa: ARG002
        return "transcript", 0.0

    async def fact_check(self, **kwargs):  # noqa: ANN003, ARG002
        self.calls += 1
        return FactCheckResult(
            needs_reply=True,
            verdict=Verdict.FALSE,
            confidence=0.95,
            canonical_claim_en="canonical claim",
            reply_language="English",
            reply_text="This is not correct.",
            evidence=[EvidenceSource(title="Gov", url="https://gov.sg", domain="gov.sg")],
            usage=ModelUsage(),
            claim_key="claim-key-1",
        )


class FakeRegistry:
    def preferred_domains(self):
        return ["gov.sg"]

    def prompt_hint(self) -> str:
        return "Prefer gov.sg"


@pytest.mark.asyncio
async def test_factcheck_service_returns_cached_hot_claim_without_model_call() -> None:
    cache = FakeCacheRepo()
    cached_result = FactCheckResult(
        needs_reply=True,
        verdict=Verdict.FALSE,
        confidence=0.91,
        canonical_claim_en="canonical claim",
        reply_language="English",
        reply_text="Cached correction",
        evidence=[EvidenceSource(title="Gov", url="https://gov.sg", domain="gov.sg")],
        usage=ModelUsage(),
        claim_key="claim-key-1",
    )
    await cache.upsert(
        claim_key="claim-key-1",
        result=cached_result,
        expires_at=datetime.now(timezone.utc),
    )

    hot_store = InMemoryHotClaimStore()
    await hot_store.replace([HotClaim(hash_key="hash-1", claim_key="claim-key-1", reason="hot", score=3)], 60)
    client = FakeClient()
    service = FactCheckService(
        client=client,
        registry=FakeRegistry(),
        cache_repo=cache,
        hot_claim_store=hot_store,
        style_profile_service=StyleProfileService(),
    )

    result = await service.assess_candidate(
        message=NormalizedMessage(
            occurred_at=datetime.now(timezone.utc),
            group_id="group-1",
            message_id="message-1",
            sender_id="sender-1",
            content_kind=ContentKind.TEXT,
            text="fake",
            text_sha256="hash-1",
        ),
        style_profile=GroupStyleProfile(),
    )

    assert result.cache_hit is True
    assert client.calls == 0
    assert result.reply_text == "Cached correction"
