"""Unit tests for fail-closed zero-trust resilience on Redis outages."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_control_plane.gateway.confirmation import RedisNonceStore
from mcp_control_plane.gateway.rate_limit import (
    InMemorySlidingWindowRateLimiter,
    RedisSlidingWindowRateLimiter,
)


@pytest.mark.asyncio
async def test_redis_rate_limiter_fail_closed_mode() -> None:
    """When fail_closed=True, Redis errors reject requests to avoid open floodgates."""
    mock_redis = MagicMock()
    # Simulate Redis connection failure on pipeline execution
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis connection lost"))
    mock_redis.pipeline.return_value = mock_pipe

    limiter = RedisSlidingWindowRateLimiter(mock_redis, fail_closed=True)
    allowed, remaining, retry_after = await limiter.check_limit("client-1", "tool-a", 100)

    assert not allowed
    assert remaining == 0
    assert retry_after == 5000


@pytest.mark.asyncio
async def test_redis_rate_limiter_fallback_to_in_memory() -> None:
    """When fallback_limiter is provided, Redis errors fall back to local bounded window."""
    mock_redis = MagicMock()
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(side_effect=TimeoutError("Redis cluster unreachable"))
    mock_redis.pipeline.return_value = mock_pipe

    local_fallback = InMemorySlidingWindowRateLimiter()
    limiter = RedisSlidingWindowRateLimiter(
        mock_redis,
        fallback_limiter=local_fallback,
        fail_closed=False,
    )

    # First request succeeds via local in-memory fallback
    allowed, remaining, retry_after = await limiter.check_limit("client-1", "tool-a", 2)
    assert allowed
    assert remaining == 1
    assert retry_after == 0

    # Second request succeeds
    allowed2, remaining2, _ = await limiter.check_limit("client-1", "tool-a", 2)
    assert allowed2
    assert remaining2 == 0

    # Third request is throttled by in-memory rate limiter (NOT open floodgate!)
    allowed3, remaining3, retry3 = await limiter.check_limit("client-1", "tool-a", 2)
    assert not allowed3
    assert remaining3 == 0
    assert retry3 > 0


@pytest.mark.asyncio
async def test_redis_nonce_store_fail_closed() -> None:
    """On Redis failure, nonce store returns False to reject mutating state changes."""
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

    nonce_store = RedisNonceStore(mock_redis)
    consumed = await nonce_store.consume_nonce("test-nonce-123", ttl_seconds=60)

    # Must fail closed: False prevents unauthorized mutating execution
    assert not consumed
