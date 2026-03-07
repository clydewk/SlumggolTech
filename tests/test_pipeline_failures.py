from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slumggol_bot.schemas import (
    CandidateDecision,
    ContentKind,
    GroupStyleProfile,
    NormalizedMessage,
)
from slumggol_bot.services.pipeline import PipelineOrchestrator


class FakeAuthenticationError(Exception):
    pass


FakeAuthenticationError.__name__ = "AuthenticationError"


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeGroup:
    def __init__(self) -> None:
        self.analysis_mode = "all_messages_llm"
        self.style_profile = {}
        self.paused = False


class FakeGroupRepo:
    def __init__(self) -> None:
        self.group = FakeGroup()

    async def get_or_create(self, external_id: str) -> FakeGroup:  # noqa: ARG002
        return self.group

    async def update_style_profile(self, group: FakeGroup, profile: GroupStyleProfile) -> None:
        group.style_profile = profile.model_dump(mode="json")


class FakeTransport:
    def __init__(self, messages: list[NormalizedMessage]) -> None:
        self.messages = messages
        self.sent_messages: list[tuple[str, str]] = []

    async def normalize_webhook(self, payload: dict) -> list[NormalizedMessage]:  # noqa: ARG002
        return self.messages

    async def send_group_message(self, group_id: str, reply_text: str) -> None:
        self.sent_messages.append((group_id, reply_text))


class FakeAnalyticsSink:
    async def write(self, events) -> None:  # noqa: ANN001, ARG002
        return None


class FakeHashObservationStore:
    async def record(self, hash_keys: list[str], group_id: str):  # noqa: ANN001, ARG002
        return []


class FakeHotClaimStore:
    async def contains_hash(self, hash_key: str) -> bool:  # noqa: ARG002
        return False

    async def claim_key_for_hash(self, hash_key: str) -> str | None:  # noqa: ARG002
        return None

    async def replace(self, claims, ttl_seconds: int) -> None:  # noqa: ANN001, ARG002
        return None


class FakeCandidateGate:
    def decide(self, **kwargs) -> CandidateDecision:  # noqa: ANN003, ARG002
        return CandidateDecision(candidate=False)


class ExplodingFactCheckService:
    async def assess_candidate(
        self,
        *,
        message: NormalizedMessage,  # noqa: ARG002
        style_profile: GroupStyleProfile,  # noqa: ARG002
    ):
        raise FakeAuthenticationError("bad key")


class FakeStyleProfileService:
    def update_profile(
        self,
        profile: GroupStyleProfile,
        message: NormalizedMessage,  # noqa: ARG002
    ) -> GroupStyleProfile:
        return GroupStyleProfile(message_count=profile.message_count + 1)


@pytest.mark.asyncio
async def test_factcheck_command_returns_user_visible_error_on_auth_failure() -> None:
    session = FakeSession()
    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-5264231879",
        message_id="-5264231879:9",
        sender_id="42",
        content_kind=ContentKind.TEXT,
        command_name="factcheck",
        command_arg_text="MOH confirmed that drinking salt water cures dengue",
        text="MOH confirmed that drinking salt water cures dengue",
    )
    transport = FakeTransport([message])
    orchestrator = PipelineOrchestrator(
        session=session,
        transport=transport,
        analytics_sink=FakeAnalyticsSink(),
        hash_observation_store=FakeHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=ExplodingFactCheckService(),
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    result = await orchestrator.process_payload({"message": {}})

    assert result == {"processed": 1, "replied": 0}
    assert session.rolled_back is True
    assert transport.sent_messages
    assert "OpenAI API key is invalid" in transport.sent_messages[0][1]
