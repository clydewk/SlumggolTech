from __future__ import annotations

from typing import Any, Protocol

from redis.asyncio import Redis

TRANSLATE_MENU_CALLBACK_DATA = "translate:menu"
TRANSLATE_LANGUAGE_CALLBACK_PREFIX = "translate:lang:"
TRANSLATION_TTL_SECONDS = 30 * 24 * 60 * 60

LANGUAGE_ORDER: tuple[str, ...] = ("en", "zh", "ms", "ta")
LANGUAGE_LABELS = {
    "en": "English",
    "zh": "中文",
    "ms": "Bahasa Melayu",
    "ta": "தமிழ்",
}
TRANSLATE_BUTTON_LABEL = "Translate, 翻译, Terjemah, மொழிபெயர்க்க"


class TranslationStateStore(Protocol):
    async def claim_language(
        self,
        *,
        group_id: str,
        source_message_id: int,
        language_code: str,
    ) -> bool: ...


class InMemoryTranslationStateStore:
    def __init__(self) -> None:
        self._claimed: dict[tuple[str, int], set[str]] = {}

    async def claim_language(
        self,
        *,
        group_id: str,
        source_message_id: int,
        language_code: str,
    ) -> bool:
        key = (group_id, source_message_id)
        current = self._claimed.setdefault(key, set())
        if language_code in current:
            return False
        current.add(language_code)
        return True


class RedisTranslationStateStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def claim_language(
        self,
        *,
        group_id: str,
        source_message_id: int,
        language_code: str,
    ) -> bool:
        key = f"translation-done:{group_id}:{source_message_id}"
        was_added = await self.redis.sadd(key, language_code)
        await self.redis.expire(key, TRANSLATION_TTL_SECONDS)
        return bool(was_added)


def parse_translation_callback_data(data: str) -> tuple[str | None, str]:
    normalized = data.strip()
    if normalized == TRANSLATE_MENU_CALLBACK_DATA:
        return "translate_menu", ""
    if normalized.startswith(TRANSLATE_LANGUAGE_CALLBACK_PREFIX):
        language_code = normalized.removeprefix(TRANSLATE_LANGUAGE_CALLBACK_PREFIX)
        if language_code in LANGUAGE_ORDER:
            return "translate_lang", language_code
    return None, ""


def translate_menu_markup() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": TRANSLATE_BUTTON_LABEL,
                    "callback_data": TRANSLATE_MENU_CALLBACK_DATA,
                }
            ]
        ]
    }


def translate_language_markup() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": LANGUAGE_LABELS["en"],
                    "callback_data": f"{TRANSLATE_LANGUAGE_CALLBACK_PREFIX}en",
                },
                {
                    "text": LANGUAGE_LABELS["zh"],
                    "callback_data": f"{TRANSLATE_LANGUAGE_CALLBACK_PREFIX}zh",
                },
            ],
            [
                {
                    "text": LANGUAGE_LABELS["ms"],
                    "callback_data": f"{TRANSLATE_LANGUAGE_CALLBACK_PREFIX}ms",
                },
                {
                    "text": LANGUAGE_LABELS["ta"],
                    "callback_data": f"{TRANSLATE_LANGUAGE_CALLBACK_PREFIX}ta",
                },
            ],
        ]
    }
