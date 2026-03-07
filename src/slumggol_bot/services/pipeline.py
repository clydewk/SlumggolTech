from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from slumggol_bot.db.repositories import ClaimCacheRepository, GroupRepository
from slumggol_bot.schemas import (
    AnalysisMode,
    AnalyticsEvent,
    CandidateDecision,
    FactCheckResult,
    GroupStyleProfile,
    NormalizedMessage,
    Verdict,
)
from slumggol_bot.services.analytics import AnalyticsSink
from slumggol_bot.services.cache import HashObservationStore, HotClaimStore
from slumggol_bot.services.factcheck import FactCheckService
from slumggol_bot.services.gating import CandidateGate
from slumggol_bot.services.hashing import compute_text_hash
from slumggol_bot.services.style_profiles import StyleProfileService
from slumggol_bot.transport.base import TransportAdapter


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        session: AsyncSession,
        transport: TransportAdapter,
        analytics_sink: AnalyticsSink,
        hash_observation_store: HashObservationStore,
        hot_claim_store: HotClaimStore,
        candidate_gate: CandidateGate,
        factcheck_service: FactCheckService,
        style_profile_service: StyleProfileService,
    ) -> None:
        self.session = session
        self.transport = transport
        self.analytics_sink = analytics_sink
        self.hash_observation_store = hash_observation_store
        self.hot_claim_store = hot_claim_store
        self.candidate_gate = candidate_gate
        self.factcheck_service = factcheck_service
        self.style_profile_service = style_profile_service
        self.group_repo = GroupRepository(session)
        self.claim_cache_repo = ClaimCacheRepository(session)

    async def process_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        messages = await self.transport.normalize_webhook(payload)
        processed = 0
        replied = 0
        for message in messages:
            result = await self.process_message(message)
            processed += 1
            if result is not None and result.needs_reply:
                replied += 1
        return {"processed": processed, "replied": replied}

    async def process_message(self, message: NormalizedMessage) -> FactCheckResult | None:
        group = await self.group_repo.get_or_create(external_id=message.group_id)
        profile = GroupStyleProfile.model_validate(group.style_profile or {})
        updated_profile = self.style_profile_service.update_profile(profile, message)
        await self.group_repo.update_style_profile(group, updated_profile)

        hash_observations = await self.hash_observation_store.record(
            message.available_hashes(),
            group_id=message.group_id,
        )
        is_hot_hash = any(await self.hot_claim_store.contains_hash(hash_key) for hash_key in message.available_hashes())
        decision = self.candidate_gate.decide(
            message=message,
            analysis_mode=AnalysisMode(group.analysis_mode),
            hash_observations=hash_observations,
            is_hot_hash=is_hot_hash,
        )
        await self.analytics_sink.write([message_event(message, decision)])

        if group.paused or not decision.candidate:
            await self.session.commit()
            return None

        result = await self.factcheck_service.assess_candidate(
            message=message,
            style_profile=updated_profile,
        )
        await self.analytics_sink.write(
            [
                claim_event(message, result),
                factcheck_event(message, result),
                usage_event(message, result),
            ]
        )
        if should_reply(result):
            await self.transport.send_group_message(message.group_id, result.reply_text)
            await self.analytics_sink.write([reply_event(message, result)])

        await self.session.commit()
        return result


def should_reply(result: FactCheckResult) -> bool:
    return (
        result.needs_reply
        and result.verdict in {Verdict.FALSE, Verdict.MISLEADING, Verdict.UNSUPPORTED}
        and result.confidence >= 0.82
        and len(result.evidence) >= 2
    )


def message_event(message: NormalizedMessage, decision: CandidateDecision) -> AnalyticsEvent:
    return AnalyticsEvent(
        table="message_events",
        payload={
            "event_id": message.message_id,
            "occurred_at": message.occurred_at,
            "group_id": message.group_id,
            "sender_hash": compute_text_hash(message.sender_id) or message.sender_id,
            "message_id": message.message_id,
            "forwarded": int(message.forwarded),
            "forwarded_many_times": int(message.forwarded_many_times),
            "content_kind": message.content_kind.value,
            "text_sha256": message.text_sha256,
            "media_sha256": message.media_sha256,
            "image_phash": message.image_phash,
            "transcript_sha256": message.transcript_sha256,
            "language_code": ",".join(message.detected_languages),
            "candidate": int(decision.candidate),
            "reason_codes": decision.reason_codes,
        },
    )


def claim_event(message: NormalizedMessage, result: FactCheckResult) -> AnalyticsEvent:
    return AnalyticsEvent(
        table="claim_events",
        payload={
            "event_id": f"{message.message_id}:claim",
            "occurred_at": datetime.now(timezone.utc),
            "group_id": message.group_id,
            "message_id": message.message_id,
            "claim_key": result.claim_key,
            "canonical_claim_en": result.canonical_claim_en,
            "reply_language": result.reply_language,
            "confidence": result.confidence,
        },
    )


def factcheck_event(message: NormalizedMessage, result: FactCheckResult) -> AnalyticsEvent:
    return AnalyticsEvent(
        table="factcheck_events",
        payload={
            "event_id": f"{message.message_id}:factcheck",
            "occurred_at": datetime.now(timezone.utc),
            "group_id": message.group_id,
            "message_id": message.message_id,
            "claim_key": result.claim_key,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "cache_hit": int(result.cache_hit),
            "needs_reply": int(result.needs_reply),
            "reason_codes": result.reason_codes,
            "source_domains": [source.domain for source in result.evidence],
        },
    )


def reply_event(message: NormalizedMessage, result: FactCheckResult) -> AnalyticsEvent:
    return AnalyticsEvent(
        table="reply_events",
        payload={
            "event_id": f"{message.message_id}:reply",
            "occurred_at": datetime.now(timezone.utc),
            "group_id": message.group_id,
            "message_id": message.message_id,
            "claim_key": result.claim_key,
            "reply_language": result.reply_language,
            "confidence": result.confidence,
            "verdict": result.verdict.value,
            "reply_count": 1,
        },
    )


def usage_event(message: NormalizedMessage, result: FactCheckResult) -> AnalyticsEvent:
    usage = result.usage
    return AnalyticsEvent(
        table="usage_events",
        payload={
            "event_id": f"{message.message_id}:usage",
            "occurred_at": datetime.now(timezone.utc),
            "group_id": message.group_id,
            "message_id": message.message_id,
            "claim_key": result.claim_key,
            "model": "gpt-5.4",
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "web_search_calls": usage.web_search_calls,
            "estimated_cost_usd": usage.estimated_cost_usd,
            "transcription_cost_usd": usage.transcription_cost_usd,
        },
    )
