from __future__ import annotations

from datetime import datetime, timezone

from slumggol_bot.schemas import AnalysisMode, ContentKind, HashObservation, NormalizedMessage
from slumggol_bot.services.gating import CandidateGate


def build_message(**overrides):
    payload = {
        "occurred_at": datetime.now(timezone.utc),
        "group_id": "group-1",
        "message_id": "message-1",
        "sender_id": "sender-1",
        "content_kind": ContentKind.TEXT,
        "text": "Forwarded fake news",
        "forwarded": True,
        "forwarded_many_times": False,
    }
    payload.update(overrides)
    return NormalizedMessage(**payload)


def test_candidate_gate_triggers_for_forwarded_cross_group_reuse() -> None:
    gate = CandidateGate()
    message = build_message()
    decision = gate.decide(
        message=message,
        analysis_mode=AnalysisMode.GATED,
        hash_observations=[HashObservation(hash_key="abc", cross_group_count=2, same_group_count=1)],
        is_hot_hash=False,
    )
    assert decision.candidate is True
    assert "forwarded_cross_group_reuse" in decision.reason_codes


def test_candidate_gate_honors_demo_override() -> None:
    gate = CandidateGate()
    message = build_message(forwarded=False)
    decision = gate.decide(
        message=message,
        analysis_mode=AnalysisMode.ALL_MESSAGES_LLM,
        hash_observations=[],
        is_hot_hash=False,
    )
    assert decision.candidate is True
    assert decision.reason_codes == ["demo_override"]
