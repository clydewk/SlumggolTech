from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def is_allowed(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> bool:
        """
        Returns True if the action is allowed, False if the limit is exceeded.
        Uses a simple Redis INCR + EXPIRE counter per window.
        """
        current = await self.redis.incr(key)
        if current == 1:
            await self.redis.expire(key, window_seconds)
        allowed = current <= limit
        if not allowed:
            logger.warning("Rate limit exceeded key=%s count=%s limit=%s", key, current, limit)
        return allowed

    async def user_allowed(self, sender_id: str, group_id: str) -> bool:
        key = f"ratelimit:user:{group_id}:{sender_id}"
        return await self.is_allowed(key, limit=5, window_seconds=60)

    async def group_allowed(self, group_id: str) -> bool:
        key = f"ratelimit:group:{group_id}"
        return await self.is_allowed(key, limit=10, window_seconds=120)

    async def user_notice_allowed(self, sender_id: str, group_id: str) -> bool:
        key = f"ratelimit:notice:user:{group_id}:{sender_id}"
        return await self._claim_notice_slot(key, window_seconds=60)

    async def group_notice_allowed(self, group_id: str) -> bool:
        key = f"ratelimit:notice:group:{group_id}"
        return await self._claim_notice_slot(key, window_seconds=120)

    async def _claim_notice_slot(self, key: str, *, window_seconds: int) -> bool:
        claimed = await self.redis.set(key, "1", ex=window_seconds, nx=True)
        return bool(claimed)
