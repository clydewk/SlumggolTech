from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings
from arq.worker import run_worker as arq_run_worker

from slumggol_bot.api.app import (
    GrouplessClaimCacheRepository,
    app,
    get_session_factory,
    get_settings,
)
from slumggol_bot.db.repositories import HotClaimRepository
from slumggol_bot.services.cache import RedisHotClaimStore
from slumggol_bot.services.outbreak import OutbreakService


async def refresh_outbreaks_job(ctx: dict) -> int:  # noqa: ARG001
    settings = get_settings()
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = OutbreakService(
            query_service=app.state.analytics_query_service,
            hot_claim_store=RedisHotClaimStore(
                app.state.redis,
                max_distance=settings.text_simhash_max_distance,
            ),
            hot_claim_repository=HotClaimRepository(session),
            claim_cache_repository=GrouplessClaimCacheRepository(session),
            lookback_minutes=settings.hot_claim_lookback_minutes,
            min_group_count=settings.hot_claim_min_groups,
        )
        claims = await service.refresh_hot_claims()
        await session.commit()
        return len(claims)


WORKER_SETTINGS: dict[str, Any] = {
    "functions": [refresh_outbreaks_job],
    "redis_settings": RedisSettings.from_dsn(get_settings().redis_url),
    "cron_jobs": [],
}


def run_worker() -> None:
    arq_run_worker(WORKER_SETTINGS)
