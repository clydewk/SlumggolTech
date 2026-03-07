from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml
from openai import AsyncOpenAI

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import (
    EvidenceSource,
    FactCheckResult,
    GroupStyleProfile,
    ModelUsage,
    NormalizedMessage,
    Verdict,
)
from slumggol_bot.services.cache import HotClaimStore
from slumggol_bot.services.hashing import compute_text_hash
from slumggol_bot.services.style_profiles import StyleProfileService


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

    def preferred_domains(self) -> list[str]:
        return [item["domain"] for item in self.domains]

    def prompt_hint(self) -> str:
        return "Prefer these Singapore-first domains when evaluating evidence: " + ", ".join(
            self.preferred_domains()
        )


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

        response = await self.client.responses.create(
            model=self.settings.openai_model,
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
            max_output_tokens=700,
            store=False,
            tools=tools,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": self.system_prompt}]},
                {"role": "user", "content": content},
            ],
        )

        parsed = json.loads(_extract_output_text(response))
        result = FactCheckResult(
            needs_reply=bool(parsed["needs_reply"]),
            verdict=Verdict(parsed["verdict"]),
            confidence=float(parsed["confidence"]),
            canonical_claim_en=parsed["canonical_claim_en"],
            reply_language=parsed.get("reply_language", "English"),
            reply_text=parsed.get("reply_text", ""),
            reason_codes=parsed.get("reason_codes", []),
            evidence=[EvidenceSource.model_validate(item) for item in parsed.get("evidence", [])],
            usage=_usage_from_response(response, self.settings, allow_web_search),
        )
        result.claim_key = compute_text_hash(result.canonical_claim_en)
        return result


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
    ) -> None:
        self.client = client
        self.registry = registry
        self.cache_repo = cache_repo
        self.hot_claim_store = hot_claim_store
        self.style_profile_service = style_profile_service

    async def assess_candidate(
        self,
        *,
        message: NormalizedMessage,
        style_profile: GroupStyleProfile,
    ) -> FactCheckResult:
        for hash_value in message.available_hashes():
            claim_key = await self.hot_claim_store.claim_key_for_hash(hash_value)
            if not claim_key:
                continue
            cached = await self.cache_repo.get(claim_key)
            if cached is None:
                continue
            return FactCheckResult(
                needs_reply=cached.verdict in {"false", "misleading", "unsupported"},
                verdict=Verdict(cached.verdict),
                confidence=float(cached.confidence),
                canonical_claim_en="",
                reply_language=cached.reply_language,
                reply_text=cached.reply_template,
                evidence=[EvidenceSource.model_validate(item) for item in cached.evidence_json],
                cache_hit=True,
                claim_key=claim_key,
            )

        transcription_cost = 0.0
        if (
            message.content_kind.value == "audio"
            and message.media_url
            and not message.transcript_text
        ):
            transcript, transcription_cost = await self.client.transcribe(message.media_url)
            message.transcript_text = transcript
            message.transcript_sha256 = compute_text_hash(transcript)

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


def _ttl_for_verdict(verdict: Verdict) -> timedelta:
    if verdict == Verdict.FALSE:
        return timedelta(days=30)
    if verdict == Verdict.MISLEADING:
        return timedelta(days=7)
    return timedelta(days=1)
