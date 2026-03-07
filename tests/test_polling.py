from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from slumggol_bot.config import AppSettings
from slumggol_bot.polling import TelegramPollingRunner


class FakeTransport:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = list(updates)
        self.deleted_webhook = False
        self.closed = False
        self.last_fetch: dict[str, int | None] | None = None

    async def fetch_updates(
        self,
        *,
        offset: int | None,
        timeout_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.last_fetch = {
            "offset": offset,
            "timeout_seconds": timeout_seconds,
            "limit": limit,
        }
        updates, self.updates = self.updates, []
        return updates

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        assert drop_pending_updates is False
        self.deleted_webhook = True

    async def aclose(self) -> None:
        self.closed = True


class FakeSession:
    pass


class FakeSessionFactory:
    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


@pytest.mark.asyncio
async def test_initialize_deletes_webhook_in_polling_mode() -> None:
    transport = FakeTransport([])
    runner = TelegramPollingRunner(
        settings=AppSettings(telegram_ingest_mode="polling"),
        session_factory=FakeSessionFactory(),
        transport=transport,
    )

    enabled = await runner.initialize()

    assert enabled is True
    assert transport.deleted_webhook is True


@pytest.mark.asyncio
async def test_run_once_processes_updates_and_advances_offset() -> None:
    processed: list[int] = []

    async def process_update(
        session: FakeSession,
        transport: Any,
        update: dict[str, Any],
    ) -> None:
        del session, transport
        processed.append(update["update_id"])

    transport = FakeTransport(
        [
            {"update_id": 41, "message": {"message_id": 1}},
            {"update_id": 42, "message": {"message_id": 2}},
        ]
    )
    runner = TelegramPollingRunner(
        settings=AppSettings(
            telegram_ingest_mode="polling",
            telegram_poll_timeout_seconds=20,
            telegram_poll_interval_seconds=0.0,
            telegram_poll_limit=10,
        ),
        session_factory=FakeSessionFactory(),
        transport=transport,
        process_update=process_update,
    )

    next_offset = await runner.run_once(offset=None)

    assert next_offset == 43
    assert processed == [41, 42]
    assert transport.last_fetch == {
        "offset": None,
        "timeout_seconds": 20,
        "limit": 10,
    }
