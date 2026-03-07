from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slumggol_bot.schemas import (
    CandidateDecision,
    ContentKind,
    EvidenceSource,
    FactCheckResult,
    GroupStyleProfile,
    ModelUsage,
    NormalizedMessage,
    TranslationResult,
    Verdict,
)
from slumggol_bot.services.pipeline import (
    PipelineOrchestrator,
    build_factcheck_command_reply,
    message_for_assessment,
)


class FakeSession:
    async def commit(self) -> None:
        return None


class FakeGroup:
    def __init__(self) -> None:
        self.analysis_mode = "gated"
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
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str, int | None, dict | None]] = []
        self.answered_callbacks: list[tuple[str, str | None]] = []
        self.edited_markups: list[tuple[str, int, dict | None]] = []

    async def normalize_webhook(self, payload: dict) -> list[NormalizedMessage]:  # noqa: ARG002
        return []

    async def send_group_message(
        self,
        group_id: str,
        reply_text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        self.sent_messages.append((group_id, reply_text, reply_to_message_id, reply_markup))

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> None:
        self.answered_callbacks.append((callback_query_id, text))

    async def edit_message_reply_markup(
        self,
        group_id: str,
        message_id: int,
        *,
        reply_markup: dict | None = None,
    ) -> None:
        self.edited_markups.append((group_id, message_id, reply_markup))


class FakeAnalyticsSink:
    def __init__(self) -> None:
        self.events = []

    async def write(self, events) -> None:  # noqa: ANN001
        self.events.extend(events)


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


class FakeFactCheckService:
    def __init__(self) -> None:
        self.messages: list[NormalizedMessage] = []
        self.followup_messages: list[NormalizedMessage] = []

    async def assess_candidate(
        self,
        *,
        message: NormalizedMessage,
        style_profile: GroupStyleProfile,  # noqa: ARG002
        language_conflict=None,  # noqa: ANN001, ARG002
    ) -> FactCheckResult:
        self.messages.append(message)
        return FactCheckResult(
            needs_reply=True,
            verdict=Verdict.FALSE,
            confidence=0.95,
            canonical_claim_en="canonical claim",
            reply_language="English",
            reply_text="This is not correct.",
            evidence=[
                EvidenceSource(title="MOH", url="https://www.moh.gov.sg", domain="moh.gov.sg"),
                EvidenceSource(title="Gov", url="https://www.gov.sg", domain="gov.sg"),
            ],
            usage=ModelUsage(),
            claim_key="claim-key-1",
        )

    async def answer_followup(
        self,
        *,
        message: NormalizedMessage,
        style_profile: GroupStyleProfile,  # noqa: ARG002
    ) -> str:
        self.followup_messages.append(message)
        return "Here are the additional reasons and evidence."

    async def translate_text(
        self,
        *,
        text: str,  # noqa: ARG002
        target_language: str,
    ) -> TranslationResult:
        if target_language == "zh":
            return TranslationResult(
                source_language="en",
                target_language="zh",
                needs_translation=True,
                translated_text="这是翻译后的版本。",
            )
        return TranslationResult(
            source_language=target_language,
            target_language=target_language,
            needs_translation=False,
            translated_text="",
        )


class FakeStyleProfileService:
    def update_profile(
        self,
        profile: GroupStyleProfile,
        message: NormalizedMessage,  # noqa: ARG002
    ) -> GroupStyleProfile:
        return GroupStyleProfile(
            dominant_languages=profile.dominant_languages,
            emoji_density=profile.emoji_density,
            average_length=profile.average_length,
            punctuation_bias=profile.punctuation_bias,
            discourse_particles=profile.discourse_particles,
            message_count=profile.message_count + 1,
        )


@pytest.mark.asyncio
async def test_factcheck_command_bypasses_gate_and_replies() -> None:
    transport = FakeTransport()
    factcheck_service = FakeFactCheckService()
    orchestrator = PipelineOrchestrator(
        session=FakeSession(),
        transport=transport,
        analytics_sink=FakeAnalyticsSink(),
        hash_observation_store=FakeHashObservationStore(),
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=factcheck_service,
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-100123",
        message_id="-100123:22",
        transport_message_id=22,
        sender_id="42",
        content_kind=ContentKind.TEXT,
        command_name="factcheck",
        command_arg_text="MOH confirmed that drinking salt water cures dengue",
        text="MOH confirmed that drinking salt water cures dengue",
        text_sha256="hash-1",
    )

    result = await orchestrator.process_message(message)

    assert result is not None
    assert (
        factcheck_service.messages[0].text
        == "MOH confirmed that drinking salt water cures dengue"
    )
    assert transport.sent_messages
    assert "Verdict: false (95% confidence)" in transport.sent_messages[0][1]
    assert "This is not correct." in transport.sent_messages[0][1]
    assert transport.sent_messages[0][2] == 22
    assert transport.sent_messages[0][3] is not None


@pytest.mark.asyncio
async def test_followup_reply_to_bot_bypasses_gate_and_threads_reply() -> None:
    transport = FakeTransport()
    factcheck_service = FakeFactCheckService()
    orchestrator = PipelineOrchestrator(
        session=FakeSession(),
        transport=transport,
        analytics_sink=FakeAnalyticsSink(),
        hash_observation_store=FakeHashObservationStore(),
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=factcheck_service,
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-100123",
        message_id="-100123:120",
        transport_message_id=120,
        sender_id="314",
        content_kind=ContentKind.TEXT,
        command_name="followup",
        text="What about these other reasons?",
        quoted_text="Verdict: false (95% confidence)",
    )

    result = await orchestrator.process_message(message)

    assert result is None
    assert len(factcheck_service.followup_messages) == 1
    assert factcheck_service.followup_messages[0].text == "What about these other reasons?"
    assert transport.sent_messages
    assert transport.sent_messages[0][1] == "Here are the additional reasons and evidence."
    assert transport.sent_messages[0][2] == 120
    assert transport.sent_messages[0][3] is not None


@pytest.mark.asyncio
async def test_translate_menu_callback_edits_keyboard_to_language_options() -> None:
    transport = FakeTransport()
    orchestrator = PipelineOrchestrator(
        session=FakeSession(),
        transport=transport,
        analytics_sink=FakeAnalyticsSink(),
        hash_observation_store=FakeHashObservationStore(),
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=FakeFactCheckService(),
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-100123",
        message_id="-100123:callback:abc",
        transport_message_id=321,
        sender_id="888",
        content_kind=ContentKind.TEXT,
        command_name="translate_menu",
        callback_query_id="abc",
        callback_data="translate:menu",
        text="Verdict: false (95% confidence)",
    )

    result = await orchestrator.process_message(message)

    assert result is None
    assert transport.edited_markups
    assert transport.edited_markups[0][0] == "-100123"
    assert transport.edited_markups[0][1] == 321
    assert transport.answered_callbacks == [("abc", "Choose language")]


@pytest.mark.asyncio
async def test_translate_lang_callback_sends_translation_once() -> None:
    transport = FakeTransport()
    orchestrator = PipelineOrchestrator(
        session=FakeSession(),
        transport=transport,
        analytics_sink=FakeAnalyticsSink(),
        hash_observation_store=FakeHashObservationStore(),
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=FakeCandidateGate(),
        factcheck_service=FakeFactCheckService(),
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-100123",
        message_id="-100123:callback:def",
        transport_message_id=654,
        sender_id="777",
        content_kind=ContentKind.TEXT,
        command_name="translate_lang",
        command_arg_text="zh",
        callback_query_id="def",
        callback_data="translate:lang:zh",
        text="This claim is false.",
    )

    first_result = await orchestrator.process_message(message)
    second_result = await orchestrator.process_message(message)

    assert first_result is None
    assert second_result is None
    assert len(transport.sent_messages) == 1
    assert transport.sent_messages[0][1] == "这是翻译后的版本。"
    assert transport.sent_messages[0][2] == 654
    assert transport.sent_messages[0][3] is not None
    assert transport.answered_callbacks[0] == ("def", "Translated to 中文")
    assert transport.answered_callbacks[1] == ("def", "Already translated to 中文.")


@pytest.mark.asyncio
async def test_auto_reply_quotes_original_message() -> None:
    class AlwaysCandidateGate:
        def decide(self, **kwargs) -> CandidateDecision:  # noqa: ANN003, ARG002
            return CandidateDecision(candidate=True)

    transport = FakeTransport()
    orchestrator = PipelineOrchestrator(
        session=FakeSession(),
        transport=transport,
        analytics_sink=FakeAnalyticsSink(),
        hash_observation_store=FakeHashObservationStore(),
        text_simhash_observation_store=FakeTextSimHashObservationStore(),
        hot_claim_store=FakeHotClaimStore(),
        candidate_gate=AlwaysCandidateGate(),
        factcheck_service=FakeFactCheckService(),
        style_profile_service=FakeStyleProfileService(),
    )
    orchestrator.group_repo = FakeGroupRepo()

    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-100123",
        message_id="-100123:99",
        transport_message_id=99,
        sender_id="42",
        content_kind=ContentKind.TEXT,
        text="Forward this warning now",
        text_sha256="hash-99",
    )

    result = await orchestrator.process_message(message)

    assert result is not None
    assert transport.sent_messages
    assert transport.sent_messages[0][2] == 99


def test_message_for_assessment_uses_quoted_text_for_factcheck_command() -> None:
    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="-100123",
        message_id="-100123:22",
        sender_id="42",
        content_kind=ContentKind.TEXT,
        command_name="factcheck",
        quoted_text="Claim to inspect",
    )

    assessment_message = message_for_assessment(message)

    assert assessment_message is not None
    assert assessment_message.text == "Claim to inspect"
    assert assessment_message.quoted_text == ""


def test_build_factcheck_command_reply_includes_sources() -> None:
    result = FactCheckResult(
        needs_reply=False,
        verdict=Verdict.UNSUPPORTED,
        confidence=0.72,
        canonical_claim_en="canonical claim",
        reply_language="English",
        reply_text="",
        evidence=[
            EvidenceSource(title="MOH", url="https://www.moh.gov.sg", domain="moh.gov.sg"),
        ],
        usage=ModelUsage(),
        claim_key="claim-key-1",
    )

    reply_text = build_factcheck_command_reply(result)

    assert "Verdict: unsupported (72% confidence)" in reply_text
    assert "I could not find strong evidence supporting this claim." in reply_text
    assert "https://www.moh.gov.sg" in reply_text
