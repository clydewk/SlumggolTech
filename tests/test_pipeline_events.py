from __future__ import annotations

from datetime import datetime, timezone

from slumggol_bot.schemas import ContentKind, FactCheckResult, ModelUsage, NormalizedMessage, Verdict
from slumggol_bot.services.pipeline import message_event


def test_message_event_contains_only_hashes_not_raw_text() -> None:
    message = NormalizedMessage(
        occurred_at=datetime.now(timezone.utc),
        group_id="group-1",
        message_id="message-1",
        sender_id="+6599990000",
        content_kind=ContentKind.TEXT,
        text="some raw text",
        quoted_text="raw quote",
        text_sha256="text-hash",
    )
    event = message_event(message, decision=type("Decision", (), {"candidate": True, "reason_codes": ["x"]})())
    assert "text" not in event.payload
    assert "quoted_text" not in event.payload
    assert event.payload["sender_hash"] != message.sender_id
