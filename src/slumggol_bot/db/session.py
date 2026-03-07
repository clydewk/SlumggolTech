from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from slumggol_bot.config import AppSettings


def create_engine(settings: AppSettings) -> AsyncEngine:
    return create_async_engine(settings.database_url, future=True, echo=False)


def create_session_factory(settings: AppSettings) -> async_sessionmaker[AsyncSession]:
    engine = create_engine(settings)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

