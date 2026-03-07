from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from redis.asyncio import Redis

from slumggol_bot.schemas import FingerprintMatchType, HashObservation, HotClaim, HotClaimMatch
from slumggol_bot.services.hashing import simhash_band_values, simhash_hamming_distance

_SIMHASH_WINDOW_HOURS = 72
_SIMHASH_MATCH_MEMBER_SEPARATOR = "|"


class HotClaimStore(Protocol):
    async def contains_hash(self, hash_key: str) -> bool: ...

    async def claim_key_for_hash(self, hash_key: str) -> str | None: ...

    async def simhash_match(
        self,
        text_simhash: str | None,
        max_distance: int,
    ) -> HotClaimMatch | None: ...

    async def replace(self, claims: list[HotClaim], ttl_seconds: int) -> None: ...


class HashObservationStore(Protocol):
    async def record(self, hash_keys: list[str], group_id: str) -> list[HashObservation]: ...


class TextSimHashObservationStore(Protocol):
    async def record(self, text_simhash: str | None, group_id: str) -> HashObservation | None: ...


class InMemoryHotClaimStore:
    def __init__(self, max_distance: int = 3) -> None:
        self.max_distance = max_distance
        self._claims: dict[str, HotClaim] = {}
        self._simhash_claims: dict[str, HotClaim] = {}

    async def contains_hash(self, hash_key: str) -> bool:
        return hash_key in self._claims

    async def claim_key_for_hash(self, hash_key: str) -> str | None:
        claim = self._claims.get(hash_key)
        return claim.claim_key if claim else None

    async def simhash_match(
        self,
        text_simhash: str | None,
        max_distance: int,
    ) -> HotClaimMatch | None:
        if not text_simhash:
            return None
        match: HotClaim | None = None
        best_distance: int | None = None
        for claim in self._simhash_claims.values():
            if not claim.text_simhash or not claim.claim_key:
                continue
            distance = simhash_hamming_distance(text_simhash, claim.text_simhash)
            if distance > max_distance:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                match = claim
        if match is None or best_distance is None or match.claim_key is None:
            return None
        return HotClaimMatch(
            claim_key=match.claim_key,
            match_type=FingerprintMatchType.SIMHASH,
            distance=best_distance,
        )

    async def replace(self, claims: list[HotClaim], ttl_seconds: int) -> None:  # noqa: ARG002
        self._claims = {claim.hash_key: claim for claim in claims}
        self._simhash_claims = {
            claim.claim_key: claim for claim in claims if claim.claim_key and claim.text_simhash
        }


class RedisHotClaimStore:
    def __init__(self, redis: Redis, max_distance: int = 3) -> None:
        self.redis = redis
        self.max_distance = max_distance

    async def contains_hash(self, hash_key: str) -> bool:
        return bool(await self.redis.exists(f"hot-hash:{hash_key}"))

    async def claim_key_for_hash(self, hash_key: str) -> str | None:
        value = await self.redis.get(f"hot-hash:{hash_key}")
        return value or None

    async def simhash_match(
        self,
        text_simhash: str | None,
        max_distance: int,
    ) -> HotClaimMatch | None:
        if not text_simhash:
            return None
        members: set[str] = set()
        band_values = simhash_band_values(text_simhash, band_count=max_distance + 1)
        for index, band_value in enumerate(band_values):
            members.update(
                await cast(
                    Awaitable[set[str]],
                    self.redis.smembers(f"hot-simhash-band:{index}:{band_value}"),
                )
            )
        best_match: HotClaimMatch | None = None
        for member in members:
            claim_key, candidate_simhash = _parse_hot_claim_member(member)
            if not claim_key or not candidate_simhash:
                continue
            distance = simhash_hamming_distance(text_simhash, candidate_simhash)
            if distance > max_distance:
                continue
            if best_match is None or distance < best_match.distance:
                best_match = HotClaimMatch(
                    claim_key=claim_key,
                    match_type=FingerprintMatchType.SIMHASH,
                    distance=distance,
                )
        return best_match

    async def replace(self, claims: list[HotClaim], ttl_seconds: int) -> None:
        current_keys = await self.redis.keys("hot-hash:*")
        if current_keys:
            await self.redis.delete(*current_keys)
        current_simhash_keys = await self.redis.keys("hot-simhash-band:*")
        if current_simhash_keys:
            await self.redis.delete(*current_simhash_keys)
        for claim in claims:
            await self.redis.set(
                f"hot-hash:{claim.hash_key}",
                claim.claim_key or "",
                ex=ttl_seconds,
            )
            if not claim.claim_key or not claim.text_simhash:
                continue
            for index, band_value in enumerate(
                simhash_band_values(claim.text_simhash, band_count=self.max_distance + 1)
            ):
                key = f"hot-simhash-band:{index}:{band_value}"
                await cast(
                    Awaitable[int],
                    self.redis.sadd(key, _hot_claim_member(claim.claim_key, claim.text_simhash)),
                )
                await self.redis.expire(key, ttl_seconds)


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


class InMemoryTextSimHashObservationStore:
    def __init__(self, max_distance: int) -> None:
        self.max_distance = max_distance
        self._members: dict[str, tuple[str, str]] = {}
        self._bands: dict[tuple[int, str], set[str]] = defaultdict(set)

    async def record(self, text_simhash: str | None, group_id: str) -> HashObservation | None:
        if not text_simhash:
            return None
        member = _simhash_observation_member(group_id, text_simhash)
        candidates: set[str] = set()
        for index, band_value in enumerate(
            simhash_band_values(text_simhash, band_count=self.max_distance + 1)
        ):
            candidates.update(self._bands[(index, band_value)])
        matched_groups = {group_id}
        same_group_count = 1
        best_distance: int | None = None
        for candidate in candidates:
            candidate_group_id, candidate_simhash = self._members[candidate]
            distance = simhash_hamming_distance(text_simhash, candidate_simhash)
            if distance > self.max_distance:
                continue
            matched_groups.add(candidate_group_id)
            if candidate_group_id == group_id:
                same_group_count += 1
            if best_distance is None or distance < best_distance:
                best_distance = distance
        self._members[member] = (group_id, text_simhash)
        for index, band_value in enumerate(
            simhash_band_values(text_simhash, band_count=self.max_distance + 1)
        ):
            self._bands[(index, band_value)].add(member)
        if best_distance is None:
            return None
        return HashObservation(
            hash_key=text_simhash,
            cross_group_count=len(matched_groups),
            same_group_count=same_group_count,
            match_type=FingerprintMatchType.SIMHASH,
            distance=best_distance,
        )


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


class RedisTextSimHashObservationStore:
    def __init__(self, redis: Redis, max_distance: int) -> None:
        self.redis = redis
        self.max_distance = max_distance

    async def record(self, text_simhash: str | None, group_id: str) -> HashObservation | None:
        if not text_simhash:
            return None
        now = datetime.now(UTC).timestamp()
        member = _simhash_observation_member(group_id, text_simhash)
        candidates: set[str] = set()
        cutoff = now - timedelta(hours=_SIMHASH_WINDOW_HOURS).total_seconds()
        for index, band_value in enumerate(
            simhash_band_values(text_simhash, band_count=self.max_distance + 1)
        ):
            key = f"text-simhash-band:{index}:{band_value}"
            await self.redis.zremrangebyscore(key, 0, cutoff)
            candidates.update(await self.redis.zrangebyscore(key, cutoff, now))
        matched_groups = {group_id}
        same_group_count = 1
        best_distance: int | None = None
        for candidate in candidates:
            candidate_group_id, candidate_simhash = _parse_simhash_observation_member(candidate)
            if not candidate_group_id or not candidate_simhash:
                continue
            distance = simhash_hamming_distance(text_simhash, candidate_simhash)
            if distance > self.max_distance:
                continue
            matched_groups.add(candidate_group_id)
            if candidate_group_id == group_id:
                same_group_count += 1
            if best_distance is None or distance < best_distance:
                best_distance = distance
        for index, band_value in enumerate(
            simhash_band_values(text_simhash, band_count=self.max_distance + 1)
        ):
            key = f"text-simhash-band:{index}:{band_value}"
            await self.redis.zadd(key, {member: now})
            await self.redis.expire(key, timedelta(hours=_SIMHASH_WINDOW_HOURS))
        if best_distance is None:
            return None
        return HashObservation(
            hash_key=text_simhash,
            cross_group_count=len(matched_groups),
            same_group_count=same_group_count,
            match_type=FingerprintMatchType.SIMHASH,
            distance=best_distance,
        )


class AnalyticsReplayBuffer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def add(self, event: dict) -> None:
        self.events.append(event)

    def dump(self) -> str:
        return json.dumps(self.events, indent=2, sort_keys=True)


def _simhash_observation_member(group_id: str, simhash: str) -> str:
    return f"{group_id}{_SIMHASH_MATCH_MEMBER_SEPARATOR}{simhash}"


def _parse_simhash_observation_member(member: str) -> tuple[str | None, str | None]:
    parts = member.split(_SIMHASH_MATCH_MEMBER_SEPARATOR, 1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def _hot_claim_member(claim_key: str, simhash: str) -> str:
    return f"{claim_key}{_SIMHASH_MATCH_MEMBER_SEPARATOR}{simhash}"


def _parse_hot_claim_member(member: str) -> tuple[str | None, str | None]:
    parts = member.split(_SIMHASH_MATCH_MEMBER_SEPARATOR, 1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]
