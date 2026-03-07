from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml  # type: ignore[import-untyped]
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import (
    Actionability,
    ClaimCategory,
    EvidenceSource,
    FactCheckResult,
    GroupStyleProfile,
    ModelUsage,
    NormalizedMessage,
    RiskLevel,
    Verdict,
)
from slumggol_bot.services.cache import HotClaimStore
from slumggol_bot.services.hashing import compute_text_hash, compute_text_simhash
from slumggol_bot.services.style_profiles import StyleProfileService

logger = logging.getLogger(__name__)
_FACTCHECK_OUTPUT_TOKEN_BUDGETS = (1200, 2200)
_OFFICIAL_SOURCE_CATEGORIES = {"government", "public_health", "public_safety"}


class ClaimCacheProtocol(Protocol):
    async def get(self, claim_key: str) -> Any | None: ...

    async def upsert(
        self,
        *,
        claim_key: str,
        result: FactCheckResult,
        expires_at: datetime,
    ) -> None: ...


class SourceRegistry:
    def __init__(self, path: Path) -> None:
        loaded = yaml.safe_load(path.read_text()) or {}
        self.domains: list[dict[str, Any]] = loaded.get("domains", [])
        self._domain_map = {
            _normalize_domain_value(str(item["domain"])): item
            for item in self.domains
            if item.get("domain")
        }

    def preferred_domains(self) -> list[str]:
        return [str(item["domain"]) for item in self.domains if item.get("domain")]

    def prompt_hint(self) -> str:
        return "Prefer these Singapore-first domains when evaluating evidence: " + ", ".join(
            self.preferred_domains()
        )

    def is_preferred_domain(self, domain: str) -> bool:
        return _normalize_domain_value(domain) in self._domain_map

    def is_official_domain(self, domain: str) -> bool:
        item = self._domain_map.get(_normalize_domain_value(domain))
        if item is None:
            return False
        category = str(item.get("category", "")).strip().lower()
        return category in _OFFICIAL_SOURCE_CATEGORIES

    def has_official_or_singapore_first_source(self, domains: list[str]) -> bool:
        return any(self.is_preferred_domain(domain) for domain in domains)

    def official_source_domain_count(self, domains: list[str]) -> int:
        return sum(1 for domain in dict.fromkeys(domains) if self.is_official_domain(domain))


class FactCheckResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    needs_reply: bool
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    canonical_claim_en: str
    reply_language: str = "English"
    reply_text: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceSource] = Field(default_factory=list)
    claim_category: ClaimCategory = ClaimCategory.OTHER
    risk_level: RiskLevel = RiskLevel.LOW
    actionability: Actionability = Actionability.MONITOR


class OpenAIFactCheckClient:
    def __init__(self, settings: AppSettings, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, http_client=http_client)
        self.system_prompt = settings.prompt_path.read_text().strip()

    async def transcribe(self, audio_url: str) -> tuple[str, float]:
        if not audio_url:
            return "", 0.0
        audio_bytes, content_type = await _download_media_bytes(audio_url)

        transcript = await self.client.audio.transcriptions.create(
            file=_transcription_upload(audio_bytes, content_type),
            model=self.settings.openai_transcribe_model,
        )
        text = getattr(transcript, "text", "") or ""
        duration_seconds = 0.0
        return text, self.settings.estimate_transcription_cost(seconds=duration_seconds)

    async def fact_check(
        self,
        *,
        message: NormalizedMessage,
        style_profile: GroupStyleProfile,
        registry: SourceRegistry,
        allow_web_search: bool,
        style_profile_service: StyleProfileService,
    ) -> FactCheckResult:
        user_text = "\n".join(
            part
            for part in [
                f"Group message text: {message.primary_text}",
                f"Quoted context: {message.quoted_text}" if message.quoted_text else "",
                (
                    f"Languages: {', '.join(message.detected_languages)}"
                    if message.detected_languages
                    else ""
                ),
                registry.prompt_hint(),
                style_profile_service.prompt_guidance(style_profile),
            ]
            if part
        )
        content: list[dict[str, Any]] = [{"type": "input_text", "text": user_text}]
        if message.content_kind.value == "image" and message.media_url:
            content.append(await _image_input_content(message.media_url, message.media_mimetype))
        tools = [{"type": "web_search_preview"}] if allow_web_search else []
        responses_api: Any = self.client.responses
        response = None
        parsed_payload = None
        for attempt, max_output_tokens in enumerate(_FACTCHECK_OUTPUT_TOKEN_BUDGETS, start=1):
            logger.info(
                (
                    "Calling OpenAI factcheck group_id=%s message_id=%s model=%s "
                    "attempt=%s max_output_tokens=%s has_text=%s has_image=%s web_search=%s"
                ),
                message.group_id,
                message.message_id,
                self.settings.openai_model,
                attempt,
                max_output_tokens,
                bool(message.primary_text),
                message.content_kind.value == "image" and bool(message.media_url),
                allow_web_search,
            )
            response = await responses_api.create(
                model=self.settings.openai_model,
                reasoning={"effort": "low"},
                text={
                    "verbosity": "low",
                    "format": _factcheck_output_format(),
                },
                max_output_tokens=max_output_tokens,
                store=False,
                tools=tools,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": self.system_prompt}],
                    },
                    {"role": "user", "content": content},
                ],
            )

            try:
                parsed_payload = FactCheckResponsePayload.model_validate_json(
                    _extract_output_text(response)
                )
                break
            except (ValidationError, ValueError):
                incomplete_reason = _response_incomplete_reason(response)
                if (
                    incomplete_reason == "max_output_tokens"
                    and attempt < len(_FACTCHECK_OUTPUT_TOKEN_BUDGETS)
                ):
                    logger.warning(
                        (
                            "Retrying incomplete OpenAI factcheck response "
                            "response_id=%s model=%s status=%s incomplete_reason=%s "
                            "max_output_tokens=%s output_chars=%s output_sha256=%s"
                        ),
                        getattr(response, "id", None),
                        self.settings.openai_model,
                        getattr(response, "status", None),
                        incomplete_reason,
                        max_output_tokens,
                        len(_safe_output_text(response)),
                        compute_text_hash(_safe_output_text(response)),
                    )
                    continue
                logger.exception(
                    (
                        "Failed to parse OpenAI factcheck response "
                        "response_id=%s model=%s status=%s incomplete_reason=%s "
                        "max_output_tokens=%s output_chars=%s output_sha256=%s"
                    ),
                    getattr(response, "id", None),
                    self.settings.openai_model,
                    getattr(response, "status", None),
                    incomplete_reason,
                    max_output_tokens,
                    len(_safe_output_text(response)),
                    compute_text_hash(_safe_output_text(response)),
                )
                raise

        if response is None or parsed_payload is None:
            raise RuntimeError("OpenAI factcheck response was not created.")

        result = FactCheckResult(
            needs_reply=parsed_payload.needs_reply,
            verdict=parsed_payload.verdict,
            confidence=parsed_payload.confidence,
            canonical_claim_en=parsed_payload.canonical_claim_en,
            canonical_text_simhash=compute_text_simhash(parsed_payload.canonical_claim_en),
            reply_language=parsed_payload.reply_language,
            reply_text=parsed_payload.reply_text,
            reason_codes=parsed_payload.reason_codes,
            evidence=parsed_payload.evidence,
            claim_category=parsed_payload.claim_category,
            risk_level=parsed_payload.risk_level,
            actionability=parsed_payload.actionability,
            usage=_usage_from_response(response, self.settings, allow_web_search),
        )
        evidence_domains = [source.domain for source in result.evidence if source.domain]
        result.has_official_sg_source = registry.has_official_or_singapore_first_source(
            evidence_domains
        )
        result.official_source_domain_count = registry.official_source_domain_count(
            evidence_domains
        )
        result.claim_key = compute_text_hash(result.canonical_claim_en)
        return result


def _factcheck_output_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "factcheck_result",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "needs_reply": {"type": "boolean"},
                "verdict": {
                    "type": "string",
                    "enum": [member.value for member in Verdict],
                },
                "confidence": {"type": "number"},
                "canonical_claim_en": {
                    "type": "string",
                    "maxLength": 240,
                },
                "reply_language": {
                    "type": "string",
                    "maxLength": 32,
                },
                "reply_text": {
                    "type": "string",
                    "maxLength": 600,
                },
                "reason_codes": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 64},
                    "maxItems": 8,
                },
                "claim_category": {
                    "type": "string",
                    "enum": [member.value for member in ClaimCategory],
                },
                "risk_level": {
                    "type": "string",
                    "enum": [member.value for member in RiskLevel],
                },
                "actionability": {
                    "type": "string",
                    "enum": [member.value for member in Actionability],
                },
                "evidence": {
                    "type": "array",
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "title": {"type": "string", "maxLength": 240},
                            "url": {"type": "string", "maxLength": 500},
                            "domain": {"type": "string", "maxLength": 120},
                            "published_at": {
                                "type": ["string", "null"],
                                "maxLength": 32,
                            },
                        },
                        "required": ["title", "url", "domain", "published_at"],
                    },
                },
            },
            "required": [
                "needs_reply",
                "verdict",
                "confidence",
                "canonical_claim_en",
                "reply_language",
                "reply_text",
                "reason_codes",
                "claim_category",
                "risk_level",
                "actionability",
                "evidence",
            ],
        },
    }


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    output = getattr(response, "output", [])
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []):
            text = getattr(content, "text", None)
            if text:
                return text
    raise ValueError("OpenAI response did not include output text.")


def _safe_output_text(response: Any) -> str:
    try:
        return _extract_output_text(response)
    except ValueError:
        return ""


def _response_incomplete_reason(response: Any) -> str | None:
    details = getattr(response, "incomplete_details", None)
    return getattr(details, "reason", None)


def _usage_from_response(
    response: Any,
    settings: AppSettings,
    allow_web_search: bool,
) -> ModelUsage:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = int(getattr(details, "reasoning_tokens", 0) or 0)
    web_search_calls = 1 if allow_web_search else 0
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        web_search_calls=web_search_calls,
        estimated_cost_usd=settings.estimate_factcheck_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            web_search_calls=web_search_calls,
        ),
    )


async def _download_media_bytes(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        return response.content, content_type.split(";", 1)[0].strip()


async def _image_input_content(media_url: str, media_mimetype: str | None) -> dict[str, Any]:
    image_bytes, content_type = await _download_media_bytes(media_url)
    mime_type = media_mimetype or content_type or "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{mime_type};base64,{encoded}",
        "detail": "auto",
    }


def _transcription_upload(audio_bytes: bytes, content_type: str) -> tuple[str, bytes, str]:
    mime_type = content_type or "audio/ogg"
    extension = _extension_for_mime_type(mime_type)
    return (f"voice-note.{extension}", audio_bytes, mime_type)


def _extension_for_mime_type(mime_type: str) -> str:
    if mime_type == "audio/mpeg":
        return "mp3"
    if mime_type == "audio/mp4":
        return "m4a"
    if mime_type == "audio/wav":
        return "wav"
    return "ogg"


class FactCheckService:
    def __init__(
        self,
        *,
        client: OpenAIFactCheckClient,
        registry: SourceRegistry,
        cache_repo: ClaimCacheProtocol,
        hot_claim_store: HotClaimStore,
        style_profile_service: StyleProfileService,
        text_simhash_max_distance: int,
    ) -> None:
        self.client = client
        self.registry = registry
        self.cache_repo = cache_repo
        self.hot_claim_store = hot_claim_store
        self.style_profile_service = style_profile_service
        self.text_simhash_max_distance = text_simhash_max_distance

    async def assess_candidate(
        self,
        *,
        message: NormalizedMessage,
        style_profile: GroupStyleProfile,
    ) -> FactCheckResult:
        cached_result = await self._cached_result_for_message(message)
        if cached_result is not None:
            return cached_result

        transcription_cost = 0.0
        if (
            message.content_kind.value == "audio"
            and message.media_url
            and not message.transcript_text
        ):
            transcript, transcription_cost = await self.client.transcribe(message.media_url)
            message.transcript_text = transcript
            message.transcript_sha256 = compute_text_hash(transcript)
            if message.text_simhash is None:
                message.text_simhash = compute_text_simhash(transcript)
            cached_result = await self._cached_result_for_message(message)
            if cached_result is not None:
                cached_result.usage.transcription_cost_usd = transcription_cost
                return cached_result

        logger.info(
            "Cache lookup exhausted; sending to OpenAI group_id=%s message_id=%s",
            message.group_id,
            message.message_id,
        )
        result = await self.client.fact_check(
            message=message,
            style_profile=style_profile,
            registry=self.registry,
            allow_web_search=True,
            style_profile_service=self.style_profile_service,
        )
        result.usage.transcription_cost_usd = transcription_cost
        if result.claim_key:
            ttl = _ttl_for_verdict(result.verdict)
            await self.cache_repo.upsert(
                claim_key=result.claim_key,
                result=result,
                expires_at=datetime.now(UTC) + ttl,
            )
        return result

    async def _cached_result_for_message(
        self,
        message: NormalizedMessage,
    ) -> FactCheckResult | None:
        for hash_value in message.available_hashes():
            logger.info(
                "Checking exact hot cache group_id=%s message_id=%s hash_key=%s",
                message.group_id,
                message.message_id,
                hash_value,
            )
            claim_key = await self.hot_claim_store.claim_key_for_hash(hash_value)
            if not claim_key:
                logger.info(
                    "Exact hot cache miss group_id=%s message_id=%s hash_key=%s",
                    message.group_id,
                    message.message_id,
                    hash_value,
                )
                continue
            logger.info(
                "Exact hot cache candidate group_id=%s message_id=%s hash_key=%s claim_key=%s",
                message.group_id,
                message.message_id,
                hash_value,
                claim_key,
            )
            cached = await self.cache_repo.get(claim_key)
            if cached is None:
                logger.info(
                    "Exact hot cache stale group_id=%s message_id=%s claim_key=%s",
                    message.group_id,
                    message.message_id,
                    claim_key,
                )
                continue
            logger.info(
                "Exact hot cache hit group_id=%s message_id=%s claim_key=%s",
                message.group_id,
                message.message_id,
                claim_key,
            )
            return _cached_factcheck_result(
                cached,
                claim_key=claim_key,
                cache_match_type="exact_hot",
                cache_match_distance=0,
            )

        logger.info(
            "Checking SimHash hot cache group_id=%s message_id=%s text_simhash=%s threshold=%s",
            message.group_id,
            message.message_id,
            message.text_simhash or "-",
            self.text_simhash_max_distance,
        )
        simhash_match = await self.hot_claim_store.simhash_match(
            message.text_simhash,
            self.text_simhash_max_distance,
        )
        if simhash_match is None:
            logger.info(
                "SimHash hot cache miss group_id=%s message_id=%s text_simhash=%s",
                message.group_id,
                message.message_id,
                message.text_simhash or "-",
            )
            return None
        logger.info(
            (
                "SimHash hot cache candidate group_id=%s message_id=%s claim_key=%s "
                "distance=%s match_type=%s"
            ),
            message.group_id,
            message.message_id,
            simhash_match.claim_key,
            simhash_match.distance,
            simhash_match.match_type.value,
        )
        cached = await self.cache_repo.get(simhash_match.claim_key)
        if cached is None:
            logger.info(
                "SimHash hot cache stale group_id=%s message_id=%s claim_key=%s",
                message.group_id,
                message.message_id,
                simhash_match.claim_key,
            )
            return None
        logger.info(
            "SimHash hot cache hit group_id=%s message_id=%s claim_key=%s distance=%s",
            message.group_id,
            message.message_id,
            simhash_match.claim_key,
            simhash_match.distance,
        )
        return _cached_factcheck_result(
            cached,
            claim_key=simhash_match.claim_key,
            cache_match_type="simhash_hot",
            cache_match_distance=simhash_match.distance,
        )


def _ttl_for_verdict(verdict: Verdict) -> timedelta:
    if verdict == Verdict.FALSE:
        return timedelta(days=30)
    if verdict == Verdict.MISLEADING:
        return timedelta(days=7)
    return timedelta(days=1)


def _cached_factcheck_result(
    cached: Any,
    *,
    claim_key: str,
    cache_match_type: str,
    cache_match_distance: int,
) -> FactCheckResult:
    return FactCheckResult(
        needs_reply=cached.verdict in {"false", "misleading", "unsupported"},
        verdict=Verdict(cached.verdict),
        confidence=float(cached.confidence),
        canonical_claim_en="",
        canonical_text_simhash=cached.canonical_text_simhash,
        reply_language=cached.reply_language,
        reply_text=cached.reply_template,
        evidence=[EvidenceSource.model_validate(item) for item in cached.evidence_json],
        claim_category=ClaimCategory(getattr(cached, "claim_category", ClaimCategory.OTHER.value)),
        risk_level=RiskLevel(getattr(cached, "risk_level", RiskLevel.LOW.value)),
        actionability=Actionability(
            getattr(cached, "actionability", Actionability.MONITOR.value)
        ),
        has_official_sg_source=bool(getattr(cached, "has_official_sg_source", False)),
        official_source_domain_count=int(
            getattr(cached, "official_source_domain_count", 0) or 0
        ),
        cache_hit=True,
        cache_match_type=cache_match_type,
        cache_match_distance=cache_match_distance,
        claim_key=claim_key,
    )


def _normalize_domain_value(domain: str) -> str:
    normalized = domain.strip().lower()
    if normalized.startswith("www."):
        return normalized[4:]
    return normalized
