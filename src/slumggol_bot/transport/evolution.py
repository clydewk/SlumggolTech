from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import ContentKind, NormalizedMessage
from slumggol_bot.services.hashing import compute_text_hash


class EvolutionTransport:
    def __init__(self, settings: AppSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.evolution_base_url,
            timeout=15.0,
            headers={"apikey": settings.evolution_api_key} if settings.evolution_api_key else {},
        )

    async def normalize_webhook(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        records = payload.get("data")
        if records is None:
            records = payload.get("messages")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return []

        messages: list[NormalizedMessage] = []
        for record in records:
            data = record.get("data", record)
            message = data.get("message", {})
            remote_jid = (
                data.get("key", {}).get("remoteJid")
                or data.get("remoteJid")
                or data.get("conversationId")
                or "unknown-group"
            )
            sender_id = (
                data.get("key", {}).get("participant")
                or data.get("participant")
                or data.get("sender")
                or "unknown-sender"
            )
            message_id = data.get("key", {}).get("id") or data.get("id") or remote_jid
            timestamp_value = data.get("messageTimestamp") or data.get("timestamp")
            occurred_at = (
                datetime.fromtimestamp(int(timestamp_value), tz=timezone.utc)
                if timestamp_value
                else datetime.now(timezone.utc)
            )

            text = (
                message.get("conversation")
                or message.get("extendedTextMessage", {}).get("text")
                or message.get("imageMessage", {}).get("caption")
                or message.get("videoMessage", {}).get("caption")
                or ""
            )
            quoted_text = (
                message.get("extendedTextMessage", {})
                .get("contextInfo", {})
                .get("quotedMessage", {})
                .get("conversation", "")
            )
            image_message = message.get("imageMessage", {})
            audio_message = message.get("audioMessage", {})
            media_url = image_message.get("url") or audio_message.get("url")
            media_mimetype = image_message.get("mimetype") or audio_message.get("mimetype")
            media_duration_seconds = audio_message.get("seconds")

            if audio_message:
                content_kind = ContentKind.AUDIO
            elif image_message:
                content_kind = ContentKind.IMAGE
            else:
                content_kind = ContentKind.TEXT

            messages.append(
                NormalizedMessage(
                    occurred_at=occurred_at,
                    group_id=remote_jid,
                    message_id=message_id,
                    sender_id=sender_id,
                    content_kind=content_kind,
                    text=text,
                    quoted_text=quoted_text,
                    caption=image_message.get("caption", ""),
                    forwarded=bool(
                        message.get("contextInfo", {}).get("isForwarded")
                        or message.get("extendedTextMessage", {})
                        .get("contextInfo", {})
                        .get("isForwarded")
                    ),
                    forwarded_many_times=bool(
                        message.get("contextInfo", {}).get("forwardingScore", 0) >= 5
                        or message.get("extendedTextMessage", {})
                        .get("contextInfo", {})
                        .get("forwardingScore", 0)
                        >= 5
                    ),
                    media_url=media_url,
                    media_mimetype=media_mimetype,
                    media_duration_seconds=float(media_duration_seconds) if media_duration_seconds else None,
                    detected_languages=[],
                    text_sha256=compute_text_hash(text),
                )
            )
        return messages

    async def send_group_message(self, group_id: str, reply_text: str) -> None:
        if not self.settings.evolution_api_key:
            return

        await self.client.post(
            f"/message/sendText/{self.settings.evolution_instance}",
            json={
                "number": group_id,
                "text": reply_text,
            },
        )

