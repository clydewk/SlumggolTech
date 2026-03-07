from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from slumggol_bot.config import AppSettings
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
from slumggol_bot.services.factcheck import FactCheckService, OpenAIFactCheckClient
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


class FakeResponsesClient:
    def __init__(self, responses) -> None:  # noqa: ANN001
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.responses[len(self.calls) - 1]


class FakeResponse:
    def __init__(
        self,
        *,
        response_id: str,
        status: str,
        output_text: str,
        incomplete_reason: str | None = None,
    ) -> None:
        self.id = response_id
        self.status = status
        self.output_text = output_text
        self.incomplete_details = (
            SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
        )
        self.usage = SimpleNamespace(
            input_tokens=123,
            output_tokens=45,
            output_tokens_details=SimpleNamespace(reasoning_tokens=6),
        )


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
        expires_at=datetime.now(UTC),
    )

    hot_store = InMemoryHotClaimStore()
    await hot_store.replace(
        [HotClaim(hash_key="hash-1", claim_key="claim-key-1", reason="hot", score=3)],
        60,
    )
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
            occurred_at=datetime.now(UTC),
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


@pytest.mark.asyncio
async def test_openai_factcheck_client_retries_truncated_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings(openai_api_key="test-key")
    client = OpenAIFactCheckClient(settings)
    fake_responses = FakeResponsesClient(
        [
            FakeResponse(
                response_id="resp_incomplete",
                status="incomplete",
                incomplete_reason="max_output_tokens",
                output_text=(
                    '{"needs_reply":true,"verdict":"false","confidence":0.99,'
                    '"canonical_claim_en":"MOH confirmed'
                ),
            ),
            FakeResponse(
                response_id="resp_complete",
                status="completed",
                output_text=(
                    '{"needs_reply":true,"verdict":"false","confidence":0.99,'
                    '"canonical_claim_en":"MOH confirmed that drinking salt water cures dengue.",'
                    '"reply_language":"English",'
                    '"reply_text":"This is false. There is no official guidance '
                    'saying salt water cures dengue.",'
                    '"reason_codes":["public_health_claim","official_sources_contradict"],'
                    '"evidence":['
                    '{"title":"MOH","url":"https://www.moh.gov.sg/example","domain":"moh.gov.sg","published_at":"2024-01-01"},'
                    '{"title":"gov.sg","url":"https://www.gov.sg/example","domain":"gov.sg","published_at":"2024-01-02"}'
                    ']}'
                ),
            ),
        ]
    )
    client.client = SimpleNamespace(responses=fake_responses)
    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-5264231879",
        message_id="-5264231879:42",
        sender_id="123",
        content_kind=ContentKind.TEXT,
        text="MOH confirmed that drinking salt water cures dengue",
    )

    with caplog.at_level(logging.WARNING):
        result = await client.fact_check(
            message=message,
            style_profile=GroupStyleProfile(),
            registry=FakeRegistry(),
            allow_web_search=True,
            style_profile_service=StyleProfileService(),
        )

    assert result.verdict == Verdict.FALSE
    assert len(result.evidence) == 2
    assert fake_responses.calls[0]["text"]["format"]["type"] == "json_schema"
    assert (
        fake_responses.calls[0]["max_output_tokens"]
        < fake_responses.calls[1]["max_output_tokens"]
    )
    assert "Retrying incomplete OpenAI factcheck response" in caplog.text
