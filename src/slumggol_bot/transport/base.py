from __future__ import annotations

from typing import Protocol

from slumggol_bot.schemas import NormalizedMessage


class TransportAdapter(Protocol):
    async def normalize_webhook(self, payload: dict) -> list[NormalizedMessage]: ...

    async def send_group_message(
        self,
        group_id: str,
        reply_text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> None: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> None: ...

    async def edit_message_reply_markup(
        self,
        group_id: str,
        message_id: int,
        *,
        reply_markup: dict | None = None,
    ) -> None: ...
