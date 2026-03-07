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
LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "zh": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "simplified chinese": "zh",
    "traditional chinese": "zh",
    "zh cn": "zh",
    "zh tw": "zh",
    "ms": "ms",
    "malay": "ms",
    "bahasa melayu": "ms",
    "ta": "ta",
    "tamil": "ta",
}


class TranslationStateStore(Protocol):
    async def remember_message_root(
        self,
        *,
        group_id: str,
        message_id: int,
        root_message_id: int,
    ) -> None: ...

    async def resolve_root_message_id(
        self,
        *,
        group_id: str,
        message_id: int,
    ) -> int: ...

    async def has_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> bool: ...

    async def mark_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> None: ...

    async def claim_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> bool: ...


class InMemoryTranslationStateStore:
    def __init__(self) -> None:
        self._claimed: dict[tuple[str, int], set[str]] = {}
        self._message_roots: dict[tuple[str, int], int] = {}

    async def remember_message_root(
        self,
        *,
        group_id: str,
        message_id: int,
        root_message_id: int,
    ) -> None:
        self._message_roots[(group_id, message_id)] = root_message_id

    async def resolve_root_message_id(
        self,
        *,
        group_id: str,
        message_id: int,
    ) -> int:
        return self._message_roots.get((group_id, message_id), message_id)

    async def has_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> bool:
        return language_code in self._claimed.get((group_id, root_message_id), set())

    async def mark_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> None:
        key = (group_id, root_message_id)
        self._claimed.setdefault(key, set()).add(language_code)

    async def claim_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> bool:
        key = (group_id, root_message_id)
        current = self._claimed.setdefault(key, set())
        if language_code in current:
            return False
        current.add(language_code)
        return True


class RedisTranslationStateStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def remember_message_root(
        self,
        *,
        group_id: str,
        message_id: int,
        root_message_id: int,
    ) -> None:
        key = f"translation-root:{group_id}:{message_id}"
        await self.redis.set(key, root_message_id, ex=TRANSLATION_TTL_SECONDS)

    async def resolve_root_message_id(
        self,
        *,
        group_id: str,
        message_id: int,
    ) -> int:
        key = f"translation-root:{group_id}:{message_id}"
        value = await self.redis.get(key)
        if value is None:
            return message_id
        try:
            return int(value)
        except ValueError:
            return message_id

    async def has_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> bool:
        key = f"translation-done:{group_id}:{root_message_id}"
        return bool(await self.redis.sismember(key, language_code))

    async def mark_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> None:
        key = f"translation-done:{group_id}:{root_message_id}"
        await self.redis.sadd(key, language_code)
        await self.redis.expire(key, TRANSLATION_TTL_SECONDS)

    async def claim_language(
        self,
        *,
        group_id: str,
        root_message_id: int,
        language_code: str,
    ) -> bool:
        key = f"translation-done:{group_id}:{root_message_id}"
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


def normalize_language_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    normalized = normalized.replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if normalized in LANGUAGE_LABELS:
        return normalized
    if normalized in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[normalized]
    if "(" in normalized:
        simplified = normalized.split("(", 1)[0].strip()
        if simplified in LANGUAGE_ALIASES:
            return LANGUAGE_ALIASES[simplified]
    return None


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
