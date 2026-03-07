from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, TypedDict

import httpx

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import ContentKind, NormalizedMessage
from slumggol_bot.services.hashing import compute_text_hash, compute_text_simhash
from slumggol_bot.services.translation import parse_translation_callback_data

_FACTCHECK_COMMAND_RE = re.compile(
    r"^/factcheck(?:@[A-Za-z0-9_]+)?(?:\s+(?P<args>.*))?$",
    re.IGNORECASE,
)
_BOT_MENTION_RE = re.compile(r"(?<!\S)@(?P<username>[A-Za-z0-9_]{5,32})\b")
logger = logging.getLogger(__name__)


class TelegramTransport:
    def __init__(self, settings: AppSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.telegram_base_url,
            timeout=15.0,
        )
        self._bot_username = _normalize_username(settings.telegram_bot_username)
        self._bot_username_lookup_attempted = self._bot_username is not None

    async def normalize_webhook(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        return await self.normalize_update(payload)

    async def normalize_update(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        callback_query = self._extract_callback_query(payload)
        if callback_query is not None:
            normalized_callback = await self._normalize_callback_query(callback_query)
            if normalized_callback is None:
                return []
            logger.info(
                (
                    "Telegram callback normalized chat_id=%s message_id=%s command=%s "
                    "callback_data=%s"
                ),
                normalized_callback.group_id,
                normalized_callback.message_id,
                normalized_callback.command_name or "-",
                normalized_callback.callback_data or "-",
            )
            return [normalized_callback]

        message = self._extract_message(payload)
        if message is None:
            logger.info("Ignoring Telegram update without message payload")
            return []

        chat = message.get("chat", {})
        if chat.get("type") not in {"group", "supergroup"}:
            logger.info("Ignoring Telegram update for unsupported chat type: %s", chat.get("type"))
            return []

        normalized = await self._normalize_message(message)
        logger.info(
            (
                "Telegram message normalized chat_id=%s message_id=%s command=%s "
                "content_kind=%s forwarded=%s has_text=%s has_media=%s"
            ),
            normalized.group_id,
            normalized.message_id,
            normalized.command_name or "-",
            normalized.content_kind.value,
            normalized.forwarded,
            bool(normalized.primary_text),
            bool(normalized.media_url),
        )
        return [normalized]

    async def fetch_updates(
        self,
        *,
        offset: int | None,
        timeout_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.settings.telegram_bot_token:
            return []
        payload: dict[str, Any] = {
            "timeout": timeout_seconds,
            "limit": limit,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = await self.client.post(
            self._api_path("getUpdates"),
            json=payload,
            timeout=max(float(timeout_seconds) + 5.0, 15.0),
        )
        response.raise_for_status()
        raw_result = response.json().get("result", [])
        return [item for item in raw_result if isinstance(item, dict)]

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        if not self.settings.telegram_bot_token:
            return
        response = await self.client.post(
            self._api_path("deleteWebhook"),
            json={"drop_pending_updates": drop_pending_updates},
        )
        response.raise_for_status()

    async def send_group_message(
        self,
        group_id: str,
        reply_text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        if not self.settings.telegram_bot_token:
            return

        payload: dict[str, Any] = {
            "chat_id": group_id,
            "text": reply_text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        response = await self.client.post(
            self._api_path("sendMessage"),
            json=payload,
        )
        response.raise_for_status()

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> None:
        if not self.settings.telegram_bot_token:
            return
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
        }
        if text:
            payload["text"] = text
        response = await self.client.post(
            self._api_path("answerCallbackQuery"),
            json=payload,
        )
        response.raise_for_status()

    async def edit_message_reply_markup(
        self,
        group_id: str,
        message_id: int,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        if not self.settings.telegram_bot_token:
            return
        payload: dict[str, Any] = {
            "chat_id": group_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = await self.client.post(
            self._api_path("editMessageReplyMarkup"),
            json=payload,
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _normalize_message(self, message: dict[str, Any]) -> NormalizedMessage:
        chat = message.get("chat", {})
        group_id = str(chat.get("id", "unknown-group"))
        raw_message_id_value = message.get("message_id")
        raw_message_id = str(
            raw_message_id_value if raw_message_id_value is not None else "unknown"
        )
        transport_message_id = self._parse_message_id(raw_message_id_value)
        message_id = f"{group_id}:{raw_message_id}"

        sender = message.get("from") or message.get("sender_chat") or {}
        sender_id = str(sender.get("id", "unknown-sender"))

        occurred_at = datetime.now(UTC)
        timestamp_value = message.get("date")
        if timestamp_value:
            occurred_at = datetime.fromtimestamp(int(timestamp_value), tz=UTC)

        text = message.get("text", "") if isinstance(message.get("text"), str) else ""
        caption = message.get("caption", "") if isinstance(message.get("caption"), str) else ""
        quoted_text = self._extract_quoted_text(message.get("reply_to_message"))
        command_name, command_arg_text = self._parse_command(text)
        if (
            command_name is None
            and await self._is_reply_factcheck_mention(
                text=text,
                reply_payload=message.get("reply_to_message"),
            )
        ):
            command_name = "factcheck"
            command_arg_text = ""
        if (
            command_name is None
            and await self._is_reply_followup_trigger(
                reply_payload=message.get("reply_to_message"),
            )
        ):
            command_name = "followup"
        normalized_text = command_arg_text if command_name == "factcheck" else text

        content_kind = ContentKind.TEXT
        media_url: str | None = None
        media_mimetype: str | None = None
        media_duration_seconds: float | None = None

        image_file_id = self._extract_image_file_id(message)
        audio_payload = self._extract_audio_payload(message)

        if audio_payload is not None:
            content_kind = ContentKind.AUDIO
            media_url = await self._resolve_file_url(audio_payload["file_id"])
            media_mimetype = audio_payload["mime_type"]
            media_duration_seconds = audio_payload["duration_seconds"]
        elif image_file_id is not None:
            content_kind = ContentKind.IMAGE
            media_url = await self._resolve_file_url(image_file_id)
            media_mimetype = self._extract_image_mimetype(message)

        hash_input = text or caption
        return NormalizedMessage(
            occurred_at=occurred_at,
            group_id=group_id,
            message_id=message_id,
            transport_message_id=transport_message_id,
            sender_id=sender_id,
            content_kind=content_kind,
            command_name=command_name,
            command_arg_text=command_arg_text,
            text=normalized_text,
            quoted_text=quoted_text,
            caption=caption,
            forwarded=bool(
                message.get("forward_origin") or message.get("is_automatic_forward")
            ),
            forwarded_many_times=False,
            media_url=media_url,
            media_mimetype=media_mimetype,
            media_duration_seconds=media_duration_seconds,
            detected_languages=[],
            text_sha256=compute_text_hash(normalized_text or quoted_text or caption or hash_input),
            text_simhash=compute_text_simhash(
                normalized_text or quoted_text or caption or hash_input
            ),
        )

    def _extract_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        candidate = payload.get("message")
        return candidate if isinstance(candidate, dict) else None

    def _extract_callback_query(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        candidate = payload.get("callback_query")
        return candidate if isinstance(candidate, dict) else None

    async def _normalize_callback_query(
        self,
        callback_query: dict[str, Any],
    ) -> NormalizedMessage | None:
        query_id = callback_query.get("id")
        if not isinstance(query_id, str) or not query_id:
            return None

        message = callback_query.get("message")
        if not isinstance(message, dict):
            return None
        chat = message.get("chat", {})
        if chat.get("type") not in {"group", "supergroup"}:
            return None

        callback_data = callback_query.get("data")
        if not isinstance(callback_data, str):
            return None
        command_name, command_arg_text = parse_translation_callback_data(callback_data)
        if command_name is None:
            return None

        group_id = str(chat.get("id", "unknown-group"))
        sender_payload = callback_query.get("from")
        sender_id = (
            str(sender_payload.get("id"))
            if isinstance(sender_payload, dict) and sender_payload.get("id") is not None
            else "unknown-sender"
        )
        raw_message_id_value = message.get("message_id")
        source_message_id = self._parse_message_id(raw_message_id_value)
        callback_message_text = ""
        for key in ("text", "caption"):
            value = message.get(key)
            if isinstance(value, str):
                callback_message_text = value
                break

        return NormalizedMessage(
            occurred_at=datetime.now(UTC),
            group_id=group_id,
            message_id=f"{group_id}:callback:{query_id}",
            transport_message_id=source_message_id,
            sender_id=sender_id,
            content_kind=ContentKind.TEXT,
            command_name=command_name,
            command_arg_text=command_arg_text,
            callback_query_id=query_id,
            callback_data=callback_data,
            text=callback_message_text,
            forwarded=False,
            forwarded_many_times=False,
            detected_languages=[],
            text_sha256=compute_text_hash(callback_message_text),
            text_simhash=compute_text_simhash(callback_message_text),
        )

    def _extract_quoted_text(self, reply_payload: Any) -> str:
        if not isinstance(reply_payload, dict):
            return ""
        for key in ("text", "caption"):
            value = reply_payload.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _extract_image_file_id(self, message: dict[str, Any]) -> str | None:
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            for photo in reversed(photos):
                if isinstance(photo, dict) and isinstance(photo.get("file_id"), str):
                    return photo["file_id"]

        document = message.get("document")
        if not isinstance(document, dict):
            return None
        mime_type = document.get("mime_type")
        if isinstance(mime_type, str) and mime_type.startswith("image/"):
            file_id = document.get("file_id")
            if isinstance(file_id, str):
                return file_id
        return None

    def _extract_image_mimetype(self, message: dict[str, Any]) -> str:
        document = message.get("document")
        if isinstance(document, dict):
            mime_type = document.get("mime_type")
            if isinstance(mime_type, str) and mime_type:
                return mime_type
        return "image/jpeg"

    def _extract_audio_payload(
        self,
        message: dict[str, Any],
    ) -> AudioPayload | None:
        voice = message.get("voice")
        if isinstance(voice, dict) and isinstance(voice.get("file_id"), str):
            return {
                "file_id": voice["file_id"],
                "mime_type": str(voice.get("mime_type") or "audio/ogg"),
                "duration_seconds": float(voice.get("duration") or 0.0),
            }

        audio = message.get("audio")
        if isinstance(audio, dict) and isinstance(audio.get("file_id"), str):
            return {
                "file_id": audio["file_id"],
                "mime_type": str(audio.get("mime_type") or "audio/mpeg"),
                "duration_seconds": float(audio.get("duration") or 0.0),
            }

        document = message.get("document")
        if not isinstance(document, dict):
            return None
        mime_type = document.get("mime_type")
        if not (isinstance(mime_type, str) and mime_type.startswith("audio/")):
            return None
        file_id = document.get("file_id")
        if not isinstance(file_id, str):
            return None
        return {
            "file_id": file_id,
            "mime_type": mime_type,
            "duration_seconds": float(document.get("duration") or 0.0),
        }

    async def _resolve_file_url(self, file_id: str) -> str | None:
        if not self.settings.telegram_bot_token:
            return None

        response = await self.client.get(
            self._api_path("getFile"),
            params={"file_id": file_id},
        )
        response.raise_for_status()
        payload = response.json()
        file_path = payload.get("result", {}).get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return None
        base_url = self.settings.telegram_base_url.rstrip("/")
        token = self.settings.telegram_bot_token
        return f"{base_url}/file/bot{token}/{file_path.lstrip('/')}"

    def _api_path(self, method: str) -> str:
        return f"/bot{self.settings.telegram_bot_token}/{method}"

    def _parse_command(self, text: str) -> tuple[str | None, str]:
        match = _FACTCHECK_COMMAND_RE.match(text.strip())
        if match is None:
            return None, ""
        return "factcheck", (match.group("args") or "").strip()

    async def _is_reply_factcheck_mention(
        self,
        *,
        text: str,
        reply_payload: Any,
    ) -> bool:
        if not isinstance(reply_payload, dict):
            return False
        mentioned_usernames = {
            match.group("username").lower()
            for match in _BOT_MENTION_RE.finditer(text)
        }
        if not mentioned_usernames:
            return False

        bot_username = await self._resolve_bot_username()
        if bot_username is None:
            return False
        return bot_username in mentioned_usernames

    async def _resolve_bot_username(self) -> str | None:
        if self._bot_username is not None:
            return self._bot_username
        if self._bot_username_lookup_attempted:
            return None

        self._bot_username_lookup_attempted = True
        if not self.settings.telegram_bot_token:
            return None

        try:
            response = await self.client.get(self._api_path("getMe"))
            response.raise_for_status()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to resolve Telegram bot username via getMe")
            return None

        payload = response.json()
        username = payload.get("result", {}).get("username")
        self._bot_username = _normalize_username(username)
        if self._bot_username is None:
            logger.warning("Telegram getMe response did not include bot username")
        return self._bot_username

    async def _is_reply_followup_trigger(
        self,
        *,
        reply_payload: Any,
    ) -> bool:
        if not isinstance(reply_payload, dict):
            return False
        sender_payload = reply_payload.get("from")
        if not isinstance(sender_payload, dict):
            return False
        if not bool(sender_payload.get("is_bot")):
            return False
        replied_username = _normalize_username(sender_payload.get("username"))
        if replied_username is None:
            return False
        bot_username = await self._resolve_bot_username()
        if bot_username is None:
            return False
        return replied_username == bot_username

    def _parse_message_id(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None


class AudioPayload(TypedDict):
    file_id: str
    mime_type: str
    duration_seconds: float


def _normalize_username(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lstrip("@").lower()
    return normalized or None
