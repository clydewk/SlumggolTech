from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Protocol

from redis.asyncio import Redis

from slumggol_bot.schemas import HashObservation, HotClaim


class HotClaimStore(Protocol):
    async def contains_hash(self, hash_key: str) -> bool: ...

    async def claim_key_for_hash(self, hash_key: str) -> str | None: ...

    async def replace(self, claims: list[HotClaim], ttl_seconds: int) -> None: ...


class HashObservationStore(Protocol):
    async def record(self, hash_keys: list[str], group_id: str) -> list[HashObservation]: ...


class InMemoryHotClaimStore:
    def __init__(self) -> None:
        self._claims: dict[str, HotClaim] = {}

    async def contains_hash(self, hash_key: str) -> bool:
        return hash_key in self._claims

    async def claim_key_for_hash(self, hash_key: str) -> str | None:
        claim = self._claims.get(hash_key)
        return claim.claim_key if claim else None

    async def replace(self, claims: list[HotClaim], ttl_seconds: int) -> None:  # noqa: ARG002
        self._claims = {claim.hash_key: claim for claim in claims}


class RedisHotClaimStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def contains_hash(self, hash_key: str) -> bool:
        return bool(await self.redis.exists(f"hot-hash:{hash_key}"))

    async def claim_key_for_hash(self, hash_key: str) -> str | None:
        value = await self.redis.get(f"hot-hash:{hash_key}")
        return value or None

    async def replace(self, claims: list[HotClaim], ttl_seconds: int) -> None:
        current_keys = await self.redis.keys("hot-hash:*")
        if current_keys:
            await self.redis.delete(*current_keys)
        for claim in claims:
            await self.redis.set(
                f"hot-hash:{claim.hash_key}",
                claim.claim_key or "",
                ex=ttl_seconds,
            )


class InMemoryHashObservationStore:
    def __init__(self) -> None:
        self._group_sets: dict[str, set[str]] = defaultdict(set)
        self._group_counters: dict[tuple[str, str], int] = defaultdict(int)

    async def record(self, hash_keys: list[str], group_id: str) -> list[HashObservation]:
        observations: list[HashObservation] = []
        for hash_key in hash_keys:
            self._group_sets[hash_key].add(group_id)
            self._group_counters[(hash_key, group_id)] += 1
            observations.append(
                HashObservation(
                    hash_key=hash_key,
                    cross_group_count=len(self._group_sets[hash_key]),
                    same_group_count=self._group_counters[(hash_key, group_id)],
                )
            )
        return observations


class RedisHashObservationStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def record(self, hash_keys: list[str], group_id: str) -> list[HashObservation]:
        observations: list[HashObservation] = []
        now = datetime.now(UTC).timestamp()
        for hash_key in hash_keys:
            groups_key = f"hash-groups:{hash_key}"
            local_key = f"hash-group-count:{hash_key}:{group_id}"
            await self.redis.zadd(groups_key, {group_id: now})
            await self.redis.expire(groups_key, timedelta(hours=72))
            same_group_count = await self.redis.incr(local_key)
            await self.redis.expire(local_key, timedelta(hours=24))
            cross_group_count = await self.redis.zcard(groups_key)
            observations.append(
                HashObservation(
                    hash_key=hash_key,
                    cross_group_count=int(cross_group_count),
                    same_group_count=int(same_group_count),
                )
            )
        return observations


class AnalyticsReplayBuffer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def add(self, event: dict) -> None:
        self.events.append(event)

    def dump(self) -> str:
        return json.dumps(self.events, indent=2, sort_keys=True)
