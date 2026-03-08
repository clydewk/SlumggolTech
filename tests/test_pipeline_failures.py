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


class FakeRateLimitError(Exception):
    pass


FakeRateLimitError.__name__ = "RateLimitError"


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

    async def get_or_create(
        self,
        external_id: str,  # noqa: ARG002
        display_name: str | None = None,  # noqa: ARG002
    ) -> FakeGroup:
        return self.group

    async def update_style_profile(self, group: FakeGroup, profile: GroupStyleProfile) -> None:
        group.style_profile = profile.model_dump(mode="json")


class FakeTransport:
    def __init__(self, messages: list[NormalizedMessage]) -> None:
        self.messages = messages
        self.sent_messages: list[tuple[str, str, int | None, dict | None]] = []
        self._next_message_id = 2000

    async def normalize_webhook(self, payload: dict) -> list[NormalizedMessage]:  # noqa: ARG002
        return self.messages

    async def send_group_message(
        self,
        group_id: str,
        reply_text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> int | None:
        self.sent_messages.append((group_id, reply_text, reply_to_message_id, reply_markup))
        self._next_message_id += 1
        return self._next_message_id

    async def answer_callback_query(
        self,
        callback_query_id: str,  # noqa: ARG002
        *,
        text: str | None = None,  # noqa: ARG002
    ) -> None:
        return None

    async def edit_message_reply_markup(
        self,
        group_id: str,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        *,
        reply_markup: dict | None = None,  # noqa: ARG002
    ) -> None:
        return None


class FakeAnalyticsSink:
    async def write(self, events) -> None:  # noqa: ANN001, ARG002
        return None


class FakeHashObservationStore:
    async def record(self, hash_keys: list[str], group_id: str):  # noqa: ANN001, ARG002
        return []


class FakeTextSimHashObservationStore:
    async def record(self, text_simhash: str | None, group_id: str):  # noqa: ANN001, ARG002
        return None


class FakeHotClaimStore:
    async def contains_hash(self, hash_key: str) -> bool:  # noqa: ARG002
        return False

    async def claim_key_for_hash(self, hash_key: str) -> str | None:  # noqa: ARG002
        return None

    async def simhash_match(self, text_simhash: str | None, max_distance: int):  # noqa: ANN001, ARG002
        return None

    async def replace(self, claims, ttl_seconds: int) -> None:  # noqa: ANN001, ARG002
        return None


class FakeCandidateGate:
    def decide(self, **kwargs) -> CandidateDecision:  # noqa: ANN003, ARG002
        return CandidateDecision(candidate=False)


class ExplodingFactCheckService:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def assess_candidate(
        self,
        *,
        message: NormalizedMessage,  # noqa: ARG002
        style_profile: GroupStyleProfile,  # noqa: ARG002
        language_conflict=None,  # noqa: ANN001, ARG002
    ):
        raise self.exc

    async def answer_followup(
        self,
        *,
        message: NormalizedMessage,  # noqa: ARG002
        style_profile: GroupStyleProfile,  # noqa: ARG002
    ) -> str:
        raise self.exc


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
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=ExplodingFactCheckService(FakeAuthenticationError("bad key")),
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    result = await orchestrator.process_payload({"message": {}})

    assert result == {"processed": 1, "replied": 0}
    assert session.rolled_back is True
    assert transport.sent_messages
    assert "OpenAI API key is invalid" in transport.sent_messages[0][1]


@pytest.mark.asyncio
async def test_factcheck_command_returns_user_visible_error_on_quota_exhaustion() -> None:
    session = FakeSession()
    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-5264231879",
        message_id="-5264231879:10",
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
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=ExplodingFactCheckService(
            FakeRateLimitError("Error code: 429 - insufficient_quota")
        ),
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    result = await orchestrator.process_payload({"message": {}})

    assert result == {"processed": 1, "replied": 0}
    assert session.rolled_back is True
    assert transport.sent_messages
    assert "OpenAI quota is exhausted" in transport.sent_messages[0][1]
