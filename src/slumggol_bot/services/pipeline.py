from __future__ import annotations

import logging
from datetime import UTC, datetime
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
from slumggol_bot.services.cache import (
    HashObservationStore,
    HotClaimStore,
    TextSimHashObservationStore,
)
from slumggol_bot.services.factcheck import FactCheckService
from slumggol_bot.services.gating import CandidateGate
from slumggol_bot.services.hashing import compute_text_hash, compute_text_simhash
from slumggol_bot.services.language import detect_conflict
from slumggol_bot.services.rate_limit import RateLimiter
from slumggol_bot.services.style_profiles import StyleProfileService
from slumggol_bot.services.translation import (
    LANGUAGE_LABELS,
    InMemoryTranslationStateStore,
    TranslationStateStore,
    normalize_language_code,
    translate_language_markup,
    translate_menu_markup,
)
from slumggol_bot.transport.base import TransportAdapter

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        session: AsyncSession,
        transport: TransportAdapter,
        analytics_sink: AnalyticsSink,
        hash_observation_store: HashObservationStore,
        text_simhash_observation_store: TextSimHashObservationStore,
        hot_claim_store: HotClaimStore,
        candidate_gate: CandidateGate,
        factcheck_service: FactCheckService,
        style_profile_service: StyleProfileService,
        translation_state_store: TranslationStateStore | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.transport = transport
        self.analytics_sink = analytics_sink
        self.hash_observation_store = hash_observation_store
        self.text_simhash_observation_store = text_simhash_observation_store
        self.hot_claim_store = hot_claim_store
        self.candidate_gate = candidate_gate
        self.factcheck_service = factcheck_service
        self.style_profile_service = style_profile_service
        self.translation_state_store = translation_state_store or InMemoryTranslationStateStore()
        self.rate_limiter = rate_limiter
        self.group_repo = GroupRepository(session)
        self.claim_cache_repo = ClaimCacheRepository(session)

    async def process_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        messages = await self.transport.normalize_webhook(payload)
        processed = 0
        replied = 0
        for message in messages:
            try:
                result = await self.process_message(message)
            except Exception as exc:  # noqa: BLE001
                await self.handle_processing_error(message, exc)
                result = None
            processed += 1
            if result is not None and result.needs_reply:
                replied += 1
        return {"processed": processed, "replied": replied}

    async def process_message(self, message: NormalizedMessage) -> FactCheckResult | None:
        if message.command_name in {"translate_menu", "translate_lang"}:
            await self._handle_translation_interaction(message)
            await self.session.commit()
            return None

        group = await self.group_repo.get_or_create(external_id=message.group_id)
        if self.rate_limiter is not None:
            if not await self.rate_limiter.user_allowed(message.sender_id, message.group_id):
                await self._send_bot_reply(
                    group_id=message.group_id,
                    reply_text=(
                        "Eh, slow down lah 🙏 You sending too many messages. "
                        "Try again in a bit."
                    ),
                    reply_to_message_id=message.transport_message_id,
                    language_code="en",
                )
                return None

            if not await self.rate_limiter.group_allowed(message.group_id):
                await self._send_bot_reply(
                    group_id=message.group_id,
                    reply_text=(
                        "This group is sending a lot of claims right now. "
                        "Give it 2 minutes and try again 🙏"
                    ),
                    reply_to_message_id=message.transport_message_id,
                    language_code="en",
                )
                return None

        profile = GroupStyleProfile.model_validate(group.style_profile or {})
        updated_profile = self.style_profile_service.update_profile(profile, message)
        await self.group_repo.update_style_profile(group, updated_profile)

        hash_observations = await self.hash_observation_store.record(
            message.available_hashes(),
            group_id=message.group_id,
        )
        simhash_observation = await self.text_simhash_observation_store.record(
            message.text_simhash,
            group_id=message.group_id,
        )
        is_hot_hash = False
        for hash_key in message.available_hashes():
            if await self.hot_claim_store.contains_hash(hash_key):
                is_hot_hash = True
                break
        logger.info(
            (
                "Heuristic inputs group_id=%s message_id=%s exact_hashes=%s hot_exact=%s "
                "text_simhash=%s simhash_cross_group=%s simhash_same_group=%s simhash_distance=%s"
            ),
            message.group_id,
            message.message_id,
            len(hash_observations),
            is_hot_hash,
            message.text_simhash or "-",
            simhash_observation.cross_group_count if simhash_observation else 0,
            simhash_observation.same_group_count if simhash_observation else 0,
            simhash_observation.distance if simhash_observation else None,
        )
        decision = explicit_command_decision(message)
        if decision is None:
            decision = self.candidate_gate.decide(
                message=message,
                analysis_mode=AnalysisMode(group.analysis_mode),
                hash_observations=hash_observations,
                simhash_observation=simhash_observation,
                is_hot_hash=is_hot_hash,
            )
        logger.info(
            (
                "Decision group_id=%s message_id=%s command=%s candidate=%s "
                "reason_codes=%s paused=%s match_type=%s match_distance=%s"
            ),
            message.group_id,
            message.message_id,
            message.command_name or "-",
            decision.candidate,
            ",".join(decision.reason_codes),
            group.paused,
            decision.match_type.value if decision.match_type else "-",
            decision.match_distance,
        )
        await self.analytics_sink.write([message_event(message, decision)])

        if group.paused or not decision.candidate:
            await self.session.commit()
            return None

        if message.command_name == "followup":
            followup_question = message.primary_text.strip()
            if not followup_question:
                await self._send_bot_reply(
                    group_id=message.group_id,
                    reply_text="Reply to the bot with a follow-up question in text.",
                    reply_to_message_id=message.transport_message_id,
                    language_code="en",
                )
                await self.session.commit()
                return None
            followup_answer = await self.factcheck_service.answer_followup(
                message=message,
                style_profile=updated_profile,
            )
            await self._send_bot_reply(
                group_id=message.group_id,
                reply_text=followup_answer,
                reply_to_message_id=message.transport_message_id,
            )
            await self.session.commit()
            return None

        assessment_message = message_for_assessment(message)
        if assessment_message is None:
            logger.info(
                "Factcheck command missing target group_id=%s message_id=%s",
                message.group_id,
                message.message_id,
            )
            await self._send_bot_reply(
                group_id=message.group_id,
                reply_text=(
                    "Usage: /factcheck <claim> or reply to a message and "
                    "mention this bot."
                ),
                reply_to_message_id=message.transport_message_id,
                language_code="en",
            )
            await self.session.commit()
            return None

        language_conflict = None
        if assessment_message.forwarded and assessment_message.detected_languages:
            language_conflict = detect_conflict(
                message_languages=assessment_message.detected_languages,
                group_languages=updated_profile.dominant_languages,
            )
            if language_conflict:
                logger.info(
                    "Language conflict detected group_id=%s message_id=%s "
                    "message_langs=%s group_langs=%s",
                    message.group_id,
                    message.message_id,
                    language_conflict.message_languages,
                    language_conflict.group_languages,
                )

        result = await self.factcheck_service.assess_candidate(
            message=assessment_message,
            style_profile=updated_profile,
            language_conflict=language_conflict,
        )

        await self.analytics_sink.write(
            [
                claim_event(message, result),
                factcheck_event(message, result),
                usage_event(message, result),
            ]
        )
        if message.command_name == "factcheck":
            reply_text = build_factcheck_command_reply(result)
            logger.info(
                (
                    "Sending factcheck command reply group_id=%s "
                    "message_id=%s verdict=%s confidence=%.2f"
                ),
                message.group_id,
                message.message_id,
                result.verdict.value,
                result.confidence,
            )
            await self._send_bot_reply(
                group_id=message.group_id,
                reply_text=reply_text,
                reply_to_message_id=message.transport_message_id,
                language_code=normalize_language_code(result.reply_language),
            )
            await self.analytics_sink.write([reply_event(message, result)])
        elif should_reply(result):
            logger.info(
                "Sending auto reply group_id=%s message_id=%s verdict=%s "
                "confidence=%.2f reply_versions=%d",
                message.group_id,
                message.message_id,
                result.verdict.value,
                result.confidence,
                len(result.reply_versions),
            )
            versions_to_send = result.reply_versions or [
                type("_V", (), {"text": result.reply_text})()
            ]
            for version in versions_to_send:
                await self._send_bot_reply(
                    group_id=message.group_id,
                    reply_text=version.text,
                    reply_to_message_id=message.transport_message_id,
                    language_code=normalize_language_code(result.reply_language),
                )
            await self.analytics_sink.write([reply_event(message, result)])

        await self.session.commit()
        return result

    async def _send_bot_reply(
        self,
        *,
        group_id: str,
        reply_text: str,
        reply_to_message_id: int | None,
        root_message_id: int | None = None,
        language_code: str | None = None,
    ) -> int | None:
        sent_message_id = await self.transport.send_group_message(
            group_id,
            reply_text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=translate_menu_markup(),
        )
        if sent_message_id is None:
            return None
        root_id = root_message_id or sent_message_id
        await self.translation_state_store.remember_message_root(
            group_id=group_id,
            message_id=sent_message_id,
            root_message_id=root_id,
        )
        if language_code in LANGUAGE_LABELS:
            await self.translation_state_store.mark_language(
                group_id=group_id,
                root_message_id=root_id,
                language_code=language_code,
            )
        return sent_message_id

    async def _handle_translation_interaction(self, message: NormalizedMessage) -> None:
        callback_query_id = message.callback_query_id
        source_message_id = message.transport_message_id
        if callback_query_id is None or source_message_id is None:
            return

        if message.command_name == "translate_menu":
            await self.transport.edit_message_reply_markup(
                message.group_id,
                source_message_id,
                reply_markup=translate_language_markup(),
            )
            await self.transport.answer_callback_query(
                callback_query_id,
                text="Choose language",
            )
            return

        target_language = message.command_arg_text.strip().lower()
        if not target_language:
            await self.transport.answer_callback_query(
                callback_query_id,
                text="Invalid translation target.",
            )
            return
        if target_language not in LANGUAGE_LABELS:
            await self.transport.answer_callback_query(
                callback_query_id,
                text="Unsupported language.",
            )
            return

        root_message_id = await self.translation_state_store.resolve_root_message_id(
            group_id=message.group_id,
            message_id=source_message_id,
        )
        already_translated = await self.translation_state_store.has_language(
            group_id=message.group_id,
            root_message_id=root_message_id,
            language_code=target_language,
        )
        if already_translated:
            await self.transport.answer_callback_query(
                callback_query_id,
                text=f"Already translated to {LANGUAGE_LABELS[target_language]}.",
            )
            return

        source_text = message.primary_text.strip()
        if not source_text:
            await self.transport.answer_callback_query(
                callback_query_id,
                text="Nothing to translate.",
            )
            return

        translation = await self.factcheck_service.translate_text(
            text=source_text,
            target_language=target_language,
        )
        source_language = normalize_language_code(translation.source_language)
        if source_language in LANGUAGE_LABELS:
            await self.translation_state_store.mark_language(
                group_id=message.group_id,
                root_message_id=root_message_id,
                language_code=source_language,
            )
        if not translation.needs_translation or not translation.translated_text.strip():
            if target_language in LANGUAGE_LABELS:
                await self.translation_state_store.mark_language(
                    group_id=message.group_id,
                    root_message_id=root_message_id,
                    language_code=target_language,
                )
            await self.transport.answer_callback_query(
                callback_query_id,
                text=(
                    f"This message is already in "
                    f"{LANGUAGE_LABELS.get(target_language, target_language)}."
                ),
            )
            return

        claimed = await self.translation_state_store.claim_language(
            group_id=message.group_id,
            root_message_id=root_message_id,
            language_code=target_language,
        )
        if not claimed:
            await self.transport.answer_callback_query(
                callback_query_id,
                text=f"Already translated to {LANGUAGE_LABELS[target_language]}.",
            )
            return

        await self._send_bot_reply(
            group_id=message.group_id,
            reply_text=translation.translated_text.strip(),
            reply_to_message_id=source_message_id,
            root_message_id=root_message_id,
        )
        await self.transport.answer_callback_query(
            callback_query_id,
            text=f"Translated to {LANGUAGE_LABELS[target_language]}",
        )

    async def handle_processing_error(
        self,
        message: NormalizedMessage,
        exc: Exception,
    ) -> None:
        logger.exception(
            "Processing failed group_id=%s message_id=%s command=%s content_kind=%s",
            message.group_id,
            message.message_id,
            message.command_name or "-",
            message.content_kind.value,
        )
        rollback = getattr(self.session, "rollback", None)
        if callable(rollback):
            await rollback()
        if message.command_name == "factcheck":
            try:
                await self._send_bot_reply(
                    group_id=message.group_id,
                    reply_text=build_factcheck_command_error_reply(exc),
                    reply_to_message_id=message.transport_message_id,
                    language_code="en",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send command error reply group_id=%s message_id=%s",
                    message.group_id,
                    message.message_id,
                )
        elif message.command_name == "followup":
            try:
                await self._send_bot_reply(
                    group_id=message.group_id,
                    reply_text="Follow-up answer is temporarily unavailable. Please try again.",
                    reply_to_message_id=message.transport_message_id,
                    language_code="en",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send follow-up error reply group_id=%s message_id=%s",
                    message.group_id,
                    message.message_id,
                )
        elif message.command_name in {"translate_menu", "translate_lang"}:
            try:
                if message.callback_query_id:
                    await self.transport.answer_callback_query(
                        message.callback_query_id,
                        text="Translation failed. Please try again.",
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send translation error callback group_id=%s message_id=%s",
                    message.group_id,
                    message.message_id,
                )


def should_reply(result: FactCheckResult) -> bool:
    return (
        result.needs_reply
        and result.verdict in {Verdict.FALSE, Verdict.MISLEADING, Verdict.UNSUPPORTED}
        and result.confidence >= 0.82
        and len(result.evidence) >= 2
    )


def explicit_command_decision(message: NormalizedMessage) -> CandidateDecision | None:
    if message.command_name not in {"factcheck", "followup", "translate_menu", "translate_lang"}:
        return None
    reason_code_by_command = {
        "factcheck": "command_factcheck",
        "followup": "command_followup",
        "translate_menu": "command_translate_menu",
        "translate_lang": "command_translate_lang",
    }
    reason_code = reason_code_by_command[message.command_name]
    return CandidateDecision(candidate=True, reason_codes=[reason_code])


def message_for_assessment(message: NormalizedMessage) -> NormalizedMessage | None:
    if message.command_name != "factcheck":
        return message

    target_text = message.command_target_text()
    if not target_text and not message.media_url:
        return None

    return message.model_copy(
        update={
            "text": target_text,
            "command_arg_text": target_text,
            "quoted_text": "",
            "caption": "",
            "text_sha256": compute_text_hash(target_text),
            "text_simhash": compute_text_simhash(target_text),
        }
    )


def build_factcheck_command_reply(result: FactCheckResult) -> str:
    verdict_label = result.verdict.value.replace("_", " ")
    confidence = f"{result.confidence:.0%}"
    summary = f"Verdict: {verdict_label} ({confidence} confidence)"
    detail = result.reply_text.strip() or fallback_factcheck_command_reply(result)
    source_lines = [
        f"- {source.title}: {source.url}"
        for source in result.evidence[:2]
        if source.title and source.url
    ]
    if not source_lines:
        return "\n".join([summary, detail])
    return "\n".join([summary, detail, "Sources:", *source_lines])


def fallback_factcheck_command_reply(result: FactCheckResult) -> str:
    if result.verdict == Verdict.NON_FACTUAL:
        return (
            "This looks like opinion or a non-factual statement, "
            "so there is nothing concrete to verify."
        )
    if result.verdict == Verdict.UNCLEAR:
        return "I could not verify this confidently enough from the available evidence."
    if result.verdict == Verdict.UNSUPPORTED:
        return "I could not find strong evidence supporting this claim."
    if result.verdict == Verdict.MISLEADING:
        return "This claim leaves out important context and is misleading."
    if result.verdict == Verdict.FALSE:
        return "This claim is false."
    return "I checked it, but the result was inconclusive."


def build_factcheck_command_error_reply(exc: Exception) -> str:
    error_name = exc.__class__.__name__
    error_text = str(exc).lower()

    if error_name == "AuthenticationError":
        return (
            "Fact-check is temporarily unavailable because the OpenAI API key is invalid. "
            "Please update the bot's OpenAI credentials and try again."
        )
    if error_name == "RateLimitError":
        if "insufficient_quota" in error_text or "quota" in error_text:
            return (
                "Fact-check is temporarily unavailable because the OpenAI quota is exhausted. "
                "Please top up billing and try again."
            )
        return (
            "Fact-check is temporarily unavailable because the OpenAI rate limit was hit. "
            "Please try again shortly."
        )
    return (
        "Fact-check is temporarily unavailable because the upstream check failed. "
        "Please try again."
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
            "text_simhash": message.text_simhash,
            "media_sha256": message.media_sha256,
            "image_phash": message.image_phash,
            "transcript_sha256": message.transcript_sha256,
            "language_code": ",".join(message.detected_languages),
            "candidate": int(decision.candidate),
            "reason_codes": decision.reason_codes,
            "heuristic_match_type": decision.match_type.value if decision.match_type else "",
            "heuristic_match_distance": decision.match_distance or 0,
        },
    )


def claim_event(message: NormalizedMessage, result: FactCheckResult) -> AnalyticsEvent:
    return AnalyticsEvent(
        table="claim_events",
        payload={
            "event_id": f"{message.message_id}:claim",
            "occurred_at": datetime.now(UTC),
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
            "occurred_at": datetime.now(UTC),
            "group_id": message.group_id,
            "message_id": message.message_id,
            "claim_key": result.claim_key,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "cache_hit": int(result.cache_hit),
            "cache_match_type": result.cache_match_type or "",
            "cache_match_distance": result.cache_match_distance or 0,
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
            "occurred_at": datetime.now(UTC),
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
            "occurred_at": datetime.now(UTC),
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
