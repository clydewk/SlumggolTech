from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slumggol_bot.schemas import (
    AnalysisMode,
    ContentKind,
    FingerprintMatchType,
    HashObservation,
    NormalizedMessage,
)
from slumggol_bot.services.cache import InMemoryTextSimHashObservationStore
from slumggol_bot.services.gating import CandidateGate
from slumggol_bot.services.hashing import compute_text_hash


def build_message(**overrides) -> NormalizedMessage:
    payload = {
        "occurred_at": datetime.now(UTC),
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
        hash_observations=[
            HashObservation(
                hash_key="abc",
                cross_group_count=2,
                same_group_count=1,
            )
        ],
        simhash_observation=None,
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
        simhash_observation=None,
        is_hot_hash=False,
    )
    assert decision.candidate is True
    assert decision.reason_codes == ["demo_override"]


def test_candidate_gate_triggers_for_forwarded_cross_group_simhash_reuse() -> None:
    gate = CandidateGate()
    message = build_message(text="forwarded warning with small edits")
    decision = gate.decide(
        message=message,
        analysis_mode=AnalysisMode.GATED,
        hash_observations=[],
        simhash_observation=HashObservation(
            hash_key="simhash-1",
            cross_group_count=2,
            same_group_count=1,
            match_type=FingerprintMatchType.SIMHASH,
            distance=2,
        ),
        is_hot_hash=False,
    )
    assert decision.candidate is True
    assert "forwarded_cross_group_reuse_simhash" in decision.reason_codes
    assert decision.match_type == FingerprintMatchType.SIMHASH
    assert decision.match_distance == 2


def test_exact_text_hash_normalization_is_unchanged() -> None:
    assert compute_text_hash("  Claim   text ") == compute_text_hash("claim text")
    assert compute_text_hash("claim text!") != compute_text_hash("claim text")


@pytest.mark.asyncio
async def test_text_simhash_observation_rejects_band_collisions_above_threshold() -> None:
    store = InMemoryTextSimHashObservationStore(max_distance=3)

    first = await store.record("0000000000000000", "group-1")
    second = await store.record("000f000000000000", "group-2")
    third = await store.record("0007000000000000", "group-3")

    assert first is None
    assert second is None
    assert third is not None
    assert third.match_type == FingerprintMatchType.SIMHASH
    assert third.distance is not None
    assert third.distance <= 3
