"""Sliding-window rate limiter with Redis sorted set and in-memory fallback."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol


class RateLimiter(Protocol):
    """Protocol for checking and updating sliding-window rate limits."""

    async def check_limit(
        self,
        client_id: str,
        tool_name: str,
        limit_rpm: int,
    ) -> tuple[bool, int, int]:
        """Check whether request is allowed under rate limit.

        Returns: (allowed: bool, remaining: int, retry_after_ms: int)
        """
        ...


class InMemorySlidingWindowRateLimiter:
    """Sliding-window rate limiter backed by in-memory timestamp deques."""

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def check_limit(
        self,
        client_id: str,
        tool_name: str,
        limit_rpm: int,
    ) -> tuple[bool, int, int]:
        key = f"{client_id}:{tool_name}"
        now = time.time()
        window_start = now - 60.0

        async with self._lock:
            timestamps = self._windows.get(key, [])
            # Evict timestamps older than 60 seconds
            timestamps = [ts for ts in timestamps if ts > window_start]

            if len(timestamps) < limit_rpm:
                timestamps.append(now)
                self._windows[key] = timestamps
                remaining = limit_rpm - len(timestamps)
                return True, remaining, 0

            # Rate limited: calculate retry_after_ms from oldest timestamp
            oldest = timestamps[0]
            retry_after_sec = max(0.0, (oldest + 60.0) - now)
            retry_after_ms = int(retry_after_sec * 1000) + 1
            self._windows[key] = timestamps
            return False, 0, retry_after_ms


class RedisSlidingWindowRateLimiter:
    """Production sliding-window rate limiter using Redis sorted sets (ZADD/ZREMRANGEBYSCORE)
    with zero-trust fail-closed protection and seamless in-memory fallback.
    """

    def __init__(
        self,
        redis_client: Any,
        fallback_limiter: InMemorySlidingWindowRateLimiter | None = None,
        fail_closed: bool = False,
    ) -> None:
        self._redis = redis_client
        self.fallback_limiter = fallback_limiter or InMemorySlidingWindowRateLimiter()
        self.fail_closed = fail_closed

    async def check_limit(
        self,
        client_id: str,
        tool_name: str,
        limit_rpm: int,
    ) -> tuple[bool, int, int]:
        key = f"mcp:rl:{client_id}:{tool_name}"
        now = time.time()
        window_start = now - 60.0

        try:
            # Atomic pipeline
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.zrange(key, 0, 0, withscores=True)
            results = await pipe.execute()

            current_count = results[1]
            oldest_entries = results[2]

            if current_count < limit_rpm:
                # Add request entry with timestamp as score and unique member
                member = f"{now}:{time.perf_counter()}"
                add_pipe = self._redis.pipeline()
                add_pipe.zadd(key, {member: now})
                add_pipe.expire(key, 65)
                await add_pipe.execute()
                remaining = limit_rpm - (current_count + 1)
                return True, max(0, remaining), 0

            # Limit exceeded: determine cooldown from oldest timestamp
            if oldest_entries:
                _, oldest_score = oldest_entries[0]
                retry_after_sec = max(0.0, (float(oldest_score) + 60.0) - now)
                retry_after_ms = int(retry_after_sec * 1000) + 1
            else:
                retry_after_ms = 1000

            return False, 0, retry_after_ms
        except Exception:
            # Zero-trust resilience: strictly avoid silent open-floodgates
            if self.fail_closed:
                return False, 0, 5000
            if self.fallback_limiter:
                return await self.fallback_limiter.check_limit(client_id, tool_name, limit_rpm)
            return False, 0, 1000
