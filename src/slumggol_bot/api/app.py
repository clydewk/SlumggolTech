from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from slumggol_bot.config import AppSettings
from slumggol_bot.db.models import ClaimCacheEntry
from slumggol_bot.db.repositories import ClaimCacheRepository, GroupRepository, HotClaimRepository
from slumggol_bot.db.session import create_session_factory
from slumggol_bot.schemas import AnalysisMode, FactCheckResult
from slumggol_bot.services.analytics import (
    ClickHouseAnalyticsQueryService,
    ClickHouseAnalyticsSink,
    FailOpenAnalyticsSink,
    NoopAnalyticsQueryService,
    NoopAnalyticsSink,
)
from slumggol_bot.services.cache import (
    InMemoryHashObservationStore,
    InMemoryTextSimHashObservationStore,
    RedisHashObservationStore,
    RedisHotClaimStore,
    RedisTextSimHashObservationStore,
)
from slumggol_bot.services.factcheck import (
    FactCheckService,
    OpenAIFactCheckClient,
    SourceRegistry,
)
from slumggol_bot.services.gating import CandidateGate
from slumggol_bot.services.outbreak import OutbreakService
from slumggol_bot.services.pipeline import PipelineOrchestrator
from slumggol_bot.services.rate_limit import RateLimiter
from slumggol_bot.services.style_profiles import StyleProfileService
from slumggol_bot.services.translation import (
    InMemoryTranslationStateStore,
    RedisTranslationStateStore,
)
from slumggol_bot.transport.base import TransportAdapter
from slumggol_bot.transport.telegram import TelegramTransport


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return create_session_factory(get_settings())


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def _build_analytics(settings: AppSettings):
    if settings.enable_clickhouse and settings.clickhouse_url:
        raw_sink = ClickHouseAnalyticsSink(settings)
        sink = FailOpenAnalyticsSink(raw_sink)
        query = ClickHouseAnalyticsQueryService(raw_sink)
        return sink, query
    return NoopAnalyticsSink(), NoopAnalyticsQueryService()


async def get_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


def build_transport(settings: AppSettings | None = None) -> TelegramTransport:
    return TelegramTransport(settings or get_settings())


def build_hot_claim_store(
    settings: AppSettings | None = None,
    redis: Redis | None = None,
) -> RedisHotClaimStore:
    resolved_settings = settings or get_settings()
    return RedisHotClaimStore(
        redis or get_redis(),
        max_distance=resolved_settings.text_simhash_max_distance,
    )


def build_pipeline_orchestrator(
    session: AsyncSession,
    *,
    transport: TransportAdapter | None = None,
) -> PipelineOrchestrator:
    settings = get_settings()
    redis = app.state.redis
    resolved_transport = transport or build_transport(settings)
    hot_claim_store = build_hot_claim_store(settings, redis)
    return PipelineOrchestrator(
        session=session,
        transport=resolved_transport,
        analytics_sink=app.state.analytics_sink,
        hash_observation_store=(
            RedisHashObservationStore(redis) if redis else InMemoryHashObservationStore()
        ),
        text_simhash_observation_store=(
            RedisTextSimHashObservationStore(
                redis,
                max_distance=settings.text_simhash_max_distance,
            )
            if redis
            else InMemoryTextSimHashObservationStore(settings.text_simhash_max_distance)
        ),
        hot_claim_store=hot_claim_store,
        candidate_gate=CandidateGate(),
        factcheck_service=FactCheckService(
            client=OpenAIFactCheckClient(settings),
            registry=SourceRegistry(settings.registry_path),
            cache_repo=GrouplessClaimCacheRepository(session),
            hot_claim_store=hot_claim_store,
            style_profile_service=StyleProfileService(),
            text_simhash_max_distance=settings.text_simhash_max_distance,
        ),
        style_profile_service=StyleProfileService(),
        translation_state_store=(
            RedisTranslationStateStore(redis) if redis else InMemoryTranslationStateStore()
        ),
        rate_limiter=RateLimiter(redis) if redis else None,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    analytics_sink, analytics_query_service = _build_analytics(settings)
    redis = get_redis()
    app = FastAPI(title="Slumggol Bot", version="0.1.0")
    app.state.settings = settings
    app.state.analytics_sink = analytics_sink
    app.state.analytics_query_service = analytics_query_service
    app.state.redis = redis
    return app


app = create_app()


@app.post("/webhooks/telegram")
async def ingest_telegram_webhook(
    payload: dict,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    telegram_secret_token: str | None = Header(  # noqa: B008
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict[str, int]:
    settings = get_settings()
    if (
        settings.telegram_webhook_secret
        and telegram_secret_token != settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret.")

    orchestrator = build_pipeline_orchestrator(session)
    return await orchestrator.process_payload(payload)


@app.post("/admin/groups/{group_external_id}/analysis-mode/{analysis_mode}")
async def set_analysis_mode(
    group_external_id: str,
    analysis_mode: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str]:
    repository = GroupRepository(session)
    try:
        mode = AnalysisMode(analysis_mode.lower())
        if mode not in {AnalysisMode.GATED, AnalysisMode.ALL_MESSAGES_LLM}:
            raise ValueError
        group = await repository.set_analysis_mode(group_external_id, mode=mode)
        await session.commit()
        return {"group_id": group.external_id, "analysis_mode": group.analysis_mode}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid analysis mode.") from exc


@app.post("/admin/groups/{group_external_id}/pause")
async def pause_group(
    group_external_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str | bool]:
    repository = GroupRepository(session)
    group = await repository.set_paused(group_external_id, paused=True)
    await session.commit()
    return {"group_id": group.external_id, "paused": group.paused}


@app.post("/admin/groups/{group_external_id}/resume")
async def resume_group(
    group_external_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str | bool]:
    repository = GroupRepository(session)
    group = await repository.set_paused(group_external_id, paused=False)
    await session.commit()
    return {"group_id": group.external_id, "paused": group.paused}


@app.get("/admin/groups/{group_external_id}/metrics")
async def get_group_metrics(group_external_id: str, hours: int = 24) -> dict:
    metrics = await app.state.analytics_query_service.get_group_metrics(group_external_id, hours)
    return metrics.model_dump(mode="json")


@app.post("/admin/outbreaks/refresh")
async def refresh_outbreaks(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, int]:
    settings = get_settings()
    service = OutbreakService(
        query_service=app.state.analytics_query_service,
        hot_claim_store=build_hot_claim_store(settings, app.state.redis),
        hot_claim_repository=HotClaimRepository(session),
        claim_cache_repository=GrouplessClaimCacheRepository(session),
        lookback_minutes=settings.hot_claim_lookback_minutes,
        min_group_count=settings.hot_claim_min_groups,
    )
    claims = await service.refresh_hot_claims()
    await session.commit()
    return {"refreshed": len(claims)}


class GrouplessClaimCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.inner = ClaimCacheRepository(session)

    async def get(self, claim_key: str) -> ClaimCacheEntry | None:
        return await self.inner.get(claim_key)

    async def upsert(
        self,
        *,
        claim_key: str,
        result: FactCheckResult,
        expires_at: datetime,
    ) -> None:
        await self.inner.upsert(claim_key=claim_key, result=result, expires_at=expires_at)

    async def text_simhashes_for_claim_keys(self, claim_keys: list[str]) -> dict[str, str]:
        return await self.inner.text_simhashes_for_claim_keys(claim_keys)
