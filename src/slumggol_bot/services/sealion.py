from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import NormalizedMessage

logger = logging.getLogger(__name__)
_SEALION_MAX_OUTPUT_TOKENS = 450
_SEA_LANGUAGE_PREFIXES = frozenset(
    {
        "ceb",
        "fil",
        "id",
        "ilo",
        "jv",
        "km",
        "lo",
        "min",
        "ms",
        "my",
        "su",
        "th",
        "tl",
        "vi",
        "war",
    }
)


class SeaLionLanguageAssist(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    source_language: str = Field(default="other")
    english_gloss: str = Field(default="", max_length=1200)
    regional_context: str = Field(default="", max_length=400)


class LanguageAssistProvider(Protocol):
    async def assist_message(
        self,
        *,
        message: NormalizedMessage,
    ) -> SeaLionLanguageAssist | None: ...


class SeaLionLanguageAssistClient:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.sealion_api_key,
            base_url=settings.sealion_base_url,
        )

    async def assist_message(
        self,
        *,
        message: NormalizedMessage,
    ) -> SeaLionLanguageAssist | None:
        if not self._should_assist(message):
            return None

        parts = [f"Message text:\n{message.primary_text.strip()}"]
        if message.quoted_text:
            parts.append(f"Quoted context:\n{message.quoted_text.strip()}")
        if message.detected_languages:
            parts.append(f"Detected languages: {', '.join(message.detected_languages)}")

        response = await self.client.chat.completions.create(
            model=self.settings.sealion_model,
            temperature=0.1,
            max_tokens=_SEALION_MAX_OUTPUT_TOKENS,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Southeast Asian language assistant for a Singapore "
                        "misinformation bot. Return JSON only with this schema: "
                        '{"source_language": string, "english_gloss": string, '
                        '"regional_context": string}. '
                        "Use english_gloss to paraphrase the message in concise plain "
                        "English. Use regional_context only for slang, idioms, or local "
                        "context that would help another model interpret the text. "
                        "Do not fact-check, cite sources, or answer the claim."
                    ),
                },
                {"role": "user", "content": "\n".join(parts)},
            ],
        )
        payload = _parse_assist_payload(_completion_text(response))
        if not payload.english_gloss and not payload.regional_context:
            return None
        return payload.model_copy(update={"model": self.settings.sealion_model})

    def _should_assist(self, message: NormalizedMessage) -> bool:
        if not message.primary_text.strip():
            return False
        if (
            self.settings.sealion_assist_on_factcheck_command
            and message.command_name == "factcheck"
        ):
            return True
        if self.settings.sealion_assist_on_forwarded_messages and message.forwarded:
            return True
        return any(_is_sea_language(language) for language in message.detected_languages)


class _SeaLionAssistPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_language: str = "other"
    english_gloss: str = ""
    regional_context: str = ""


def _is_sea_language(language_code: str) -> bool:
    prefix = language_code.strip().lower().split("-", 1)[0]
    return prefix in _SEA_LANGUAGE_PREFIXES


def _completion_text(response: Any) -> str:
    choices = getattr(response, "choices", [])
    if not choices:
        raise ValueError("Sea-Lion response did not include choices.")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise ValueError("Sea-Lion response did not include a message.")
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    text_parts.append(text_value)
                    continue
            text_value = getattr(item, "text", None)
            if isinstance(text_value, str):
                text_parts.append(text_value)
        if text_parts:
            return "\n".join(text_parts)
    raise ValueError("Sea-Lion response did not include text content.")


def _parse_assist_payload(text: str) -> SeaLionLanguageAssist:
    payload = _SeaLionAssistPayload.model_validate(json.loads(_extract_json_object(text)))
    return SeaLionLanguageAssist(
        model="",
        source_language=payload.source_language.strip().lower() or "other",
        english_gloss=payload.english_gloss.strip(),
        regional_context=payload.regional_context.strip(),
    )


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Sea-Lion response did not include a JSON object.")
    return stripped[start : end + 1]
