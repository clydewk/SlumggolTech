from __future__ import annotations

from datetime import UTC, datetime

from slumggol_bot.schemas import (
    ClaimCategory,
    ContentKind,
    FactCheckResult,
    ModelUsage,
    NormalizedMessage,
    Verdict,
)
from slumggol_bot.services.pipeline import (
    claim_event,
    factcheck_event,
    message_event,
    reply_event,
    usage_event,
)


def test_message_event_contains_only_hashes_not_raw_text() -> None:
    message = NormalizedMessage(
        occurred_at=datetime.now(UTC),
        group_id="group-1",
        message_id="message-1",
        sender_id="+6599990000",
        content_kind=ContentKind.TEXT,
        text="some raw text",
        quoted_text="raw quote",
        text_sha256="text-hash",
    )
    decision = type(
        "Decision",
        (),
        {
            "candidate": True,
            "reason_codes": ["x"],
            "match_type": None,
            "match_distance": None,
        },
    )()
    event = message_event(message, decision=decision)
    assert "text" not in event.payload
    assert "quoted_text" not in event.payload
    assert event.payload["sender_hash"] != message.sender_id


def test_factcheck_related_events_use_message_timestamp_and_structured_fields() -> None:
    occurred_at = datetime.now(UTC)
    message = NormalizedMessage(
        occurred_at=occurred_at,
        group_id="group-1",
        group_display_name="Group One",
        message_id="message-1",
        sender_id="42",
        content_kind=ContentKind.TEXT,
        text_sha256="text-hash",
    )
    result = FactCheckResult(
        needs_reply=True,
        verdict=Verdict.FALSE,
        confidence=0.94,
        canonical_claim_en="canonical claim",
        reply_language="English",
        reply_text="Countermessage",
        claim_category=ClaimCategory.PUBLIC_HEALTH,
        has_official_sg_source=True,
        official_source_domain_count=2,
        usage=ModelUsage(
            model="gpt-5.4",
            auxiliary_model="aisingapore/Gemma-SEA-LION-v4-27B-IT",
            input_tokens=1,
            output_tokens=1,
        ),
        claim_key="claim-key-1",
    )

    claim = claim_event(message, result)
    factcheck = factcheck_event(message, result)
    reply = reply_event(message, result)
    usage = usage_event(message, result)

    assert claim.payload["occurred_at"] == occurred_at
    assert factcheck.payload["occurred_at"] == occurred_at
    assert reply.payload["occurred_at"] == occurred_at
    assert usage.payload["occurred_at"] == occurred_at
    assert claim.payload["group_display_name"] == "Group One"
    assert claim.payload["claim_category"] == "public_health"
    assert factcheck.payload["has_official_sg_source"] == 1
    assert reply.payload["official_source_domain_count"] == 2
    assert usage.payload["model"] == "gpt-5.4"
    assert usage.payload["auxiliary_model"] == "aisingapore/Gemma-SEA-LION-v4-27B-IT"
