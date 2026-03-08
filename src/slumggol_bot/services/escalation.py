from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slumggol_bot.db.models import EscalationQueueEntry
from slumggol_bot.schemas import FactCheckResult, NormalizedMessage, Verdict

logger = logging.getLogger(__name__)

ESCALATION_VERDICTS = {Verdict.UNCLEAR, Verdict.UNSUPPORTED}
ESCALATION_MIN_CONFIDENCE = 0.5


def should_escalate(result: FactCheckResult) -> bool:
    return (
        result.verdict in ESCALATION_VERDICTS
        and result.confidence >= ESCALATION_MIN_CONFIDENCE
    )


class EscalationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        message: NormalizedMessage,
        result: FactCheckResult,
    ) -> EscalationQueueEntry:
        entry = EscalationQueueEntry(
            group_id=message.group_id,
            message_id=message.message_id,
            claim_key=result.claim_key,
            canonical_claim_en=result.canonical_claim_en,
            verdict=result.verdict.value,
            confidence=result.confidence,
            evidence_json=[e.model_dump() for e in result.evidence],
            status="pending",
        )
        self.session.add(entry)
        await self.session.flush()
        logger.info(
            "Escalation created group_id=%s message_id=%s verdict=%s confidence=%.2f",
            message.group_id,
            message.message_id,
            result.verdict.value,
            result.confidence,
        )
        return entry

    async def list_pending(self) -> list[EscalationQueueEntry]:
        result = await self.session.execute(
            select(EscalationQueueEntry)
            .where(EscalationQueueEntry.status == "pending")
            .order_by(EscalationQueueEntry.created_at.asc())
        )
        return list(result.scalars().all())

    async def get(self, escalation_id: str) -> EscalationQueueEntry | None:
        return await self.session.get(EscalationQueueEntry, escalation_id)

    async def resolve(
        self,
        entry: EscalationQueueEntry,
        *,
        status: str,
        reviewer_note: str | None = None,
        corrected_reply: str | None = None,
    ) -> EscalationQueueEntry:
        entry.status = status
        entry.reviewer_note = reviewer_note
        entry.corrected_reply = corrected_reply
        entry.resolved_at = datetime.now(UTC)
        await self.session.flush()
        logger.info(
            "Escalation resolved id=%s status=%s",
            entry.id,
            status,
        )
        return entry
