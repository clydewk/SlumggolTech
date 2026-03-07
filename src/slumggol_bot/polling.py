from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from slumggol_bot.api.app import (
    build_pipeline_orchestrator,
    build_transport,
    get_session_factory,
    get_settings,
)
from slumggol_bot.config import AppSettings
from slumggol_bot.transport.base import TransportAdapter

logger = logging.getLogger(__name__)

class PollingTransport(TransportAdapter, Protocol):
    async def fetch_updates(
        self,
        *,
        offset: int | None,
        timeout_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None: ...

    async def aclose(self) -> None: ...


class SessionFactory[SessionT](Protocol):
    def __call__(self) -> AbstractAsyncContextManager[SessionT]: ...


class ProcessUpdate[SessionT](Protocol):
    async def __call__(
        self,
        session: SessionT,
        transport: PollingTransport,
        update: dict[str, Any],
    ) -> None: ...


class TelegramPollingRunner[SessionT]:
    def __init__(
        self,
        *,
        settings: AppSettings,
        session_factory: SessionFactory[SessionT],
        transport: PollingTransport,
        process_update: ProcessUpdate[SessionT] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.transport = transport
        self.process_update = process_update or process_polled_update
        self.sleep = sleep

    async def initialize(self) -> bool:
        if self.settings.telegram_ingest_mode.lower() != "polling":
            logger.info(
                "Telegram polling disabled because TELEGRAM_INGEST_MODE=%s",
                self.settings.telegram_ingest_mode,
            )
            return False
        await self.transport.delete_webhook(drop_pending_updates=False)
        logger.info(
            "Telegram polling started timeout=%ss interval=%ss limit=%s",
            self.settings.telegram_poll_timeout_seconds,
            self.settings.telegram_poll_interval_seconds,
            self.settings.telegram_poll_limit,
        )
        return True

    async def run(self) -> None:
        if not await self.initialize():
            await self.transport.aclose()
            return
        offset: int | None = None
        try:
            while True:
                try:
                    offset = await self.run_once(offset)
                except Exception:  # noqa: BLE001
                    logger.exception("Telegram polling iteration failed")
                    await self.sleep(self.settings.telegram_poll_interval_seconds)
        finally:
            await self.transport.aclose()

    async def run_once(self, offset: int | None) -> int | None:
        updates = await self.transport.fetch_updates(
            offset=offset,
            timeout_seconds=self.settings.telegram_poll_timeout_seconds,
            limit=self.settings.telegram_poll_limit,
        )
        if not updates:
            await self.sleep(self.settings.telegram_poll_interval_seconds)
            return offset
        next_offset = offset
        for update in updates:
            async with self.session_factory() as session:
                await self.process_update(session, self.transport, update)
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = update_id + 1
        return next_offset


async def process_polled_update(
    session: AsyncSession,
    transport: PollingTransport,
    update: dict[str, Any],
) -> None:
    orchestrator = build_pipeline_orchestrator(session, transport=transport)
    await orchestrator.process_payload(update)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    runner = TelegramPollingRunner(
        settings=settings,
        session_factory=get_session_factory(),
        transport=build_transport(settings),
    )
    asyncio.run(runner.run())
