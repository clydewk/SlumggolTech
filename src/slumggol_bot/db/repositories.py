from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from slumggol_bot.db.models import ClaimCacheEntry, Group, HotClaimEntry
from slumggol_bot.schemas import AnalysisMode, FactCheckResult, GroupStyleProfile, HotClaim


class GroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, external_id: str, display_name: str | None = None) -> Group:
        result = await self.session.execute(select(Group).where(Group.external_id == external_id))
        group = result.scalar_one_or_none()
        if group is not None:
            if display_name and not group.display_name:
                group.display_name = display_name
            return group

        group = Group(external_id=external_id, display_name=display_name)
        self.session.add(group)
        await self.session.flush()
        return group

    async def set_analysis_mode(self, external_id: str, mode: AnalysisMode) -> Group:
        group = await self.get_or_create(external_id=external_id)
        group.analysis_mode = mode.value
        await self.session.flush()
        return group

    async def set_paused(self, external_id: str, paused: bool) -> Group:
        group = await self.get_or_create(external_id=external_id)
        group.paused = paused
        await self.session.flush()
        return group

    async def update_style_profile(self, group: Group, profile: GroupStyleProfile) -> None:
        group.style_profile = profile.model_dump(mode="json")
        await self.session.flush()


class ClaimCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, claim_key: str) -> ClaimCacheEntry | None:
        entry = await self.session.get(ClaimCacheEntry, claim_key)
        if entry is None:
            return None
        if entry.expires_at <= datetime.now(timezone.utc):
            return None
        entry.last_used_at = datetime.now(timezone.utc)
        await self.session.flush()
        return entry

    async def upsert(
        self,
        *,
        claim_key: str,
        result: FactCheckResult,
        expires_at: datetime,
    ) -> None:
        entry = await self.session.get(ClaimCacheEntry, claim_key)
        if entry is None:
            entry = ClaimCacheEntry(
                claim_key=claim_key,
                verdict=result.verdict.value,
                confidence=result.confidence,
                reply_language=result.reply_language,
                reply_template=result.reply_text,
                evidence_json=[item.model_dump(mode="json") for item in result.evidence],
                source_quality_score=float(len(result.evidence)),
                expires_at=expires_at,
            )
            self.session.add(entry)
        else:
            entry.verdict = result.verdict.value
            entry.confidence = result.confidence
            entry.reply_language = result.reply_language
            entry.reply_template = result.reply_text
            entry.evidence_json = [item.model_dump(mode="json") for item in result.evidence]
            entry.source_quality_score = float(len(result.evidence))
            entry.expires_at = expires_at
            entry.last_used_at = datetime.now(timezone.utc)
        await self.session.flush()


class HotClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_active(self, claims: Iterable[HotClaim], expires_at: datetime) -> None:
        await self.session.execute(delete(HotClaimEntry))
        for claim in claims:
            self.session.add(
                HotClaimEntry(
                    hash_key=claim.hash_key,
                    claim_key=claim.claim_key,
                    reason=claim.reason,
                    score=claim.score,
                    expires_at=expires_at,
                )
            )
        await self.session.flush()
