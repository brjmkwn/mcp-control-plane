"""Unit tests for sliding-window rate limiting and async circuit breaker."""

from __future__ import annotations

import asyncio

import pytest

from mcp_control_plane.core.errors import CircuitOpenError
from mcp_control_plane.gateway.circuit_breaker import CircuitBreaker, CircuitState
from mcp_control_plane.gateway.rate_limit import InMemorySlidingWindowRateLimiter


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_allows_and_throttles() -> None:
    limiter = InMemorySlidingWindowRateLimiter()
    client = "client-alpha"
    tool = "run_query"
    limit_rpm = 5

    # First 5 calls are allowed
    for i in range(limit_rpm):
        allowed, remaining, retry_after_ms = await limiter.check_limit(client, tool, limit_rpm)
        assert allowed is True
        assert remaining == (limit_rpm - 1 - i)
        assert retry_after_ms == 0

    # 6th call is throttled
    allowed, remaining, retry_after_ms = await limiter.check_limit(client, tool, limit_rpm)
    assert allowed is False
    assert remaining == 0
    assert retry_after_ms > 0


@pytest.mark.asyncio
async def test_circuit_breaker_transitions() -> None:
    breaker = CircuitBreaker("test_upstream", failure_threshold=3, recovery_timeout_seconds=0.1)
    assert breaker.state == CircuitState.CLOSED

    async def succeeding_call():
        return "ok"

    async def failing_call():
        raise RuntimeError("upstream exploded")

    # Success keeps closed
    res = await breaker.execute(succeeding_call)
    assert res == "ok"
    assert breaker.state == CircuitState.CLOSED

    # 1st failure
    with pytest.raises(RuntimeError):
        await breaker.execute(failing_call)
    assert breaker.state == CircuitState.CLOSED

    # 2nd failure
    with pytest.raises(RuntimeError):
        await breaker.execute(failing_call)
    assert breaker.state == CircuitState.CLOSED

    # 3rd failure trips to OPEN
    with pytest.raises(RuntimeError):
        await breaker.execute(failing_call)
    assert breaker.state == CircuitState.OPEN

    # While OPEN, immediate fast-fail with CircuitOpenError
    with pytest.raises(CircuitOpenError):
        await breaker.execute(succeeding_call)

    # Wait for recovery timeout
    await asyncio.sleep(0.12)

    # Next call probes in HALF_OPEN and on success recovers to CLOSED
    res = await breaker.execute(succeeding_call)
    assert res == "ok"
    assert breaker.state == CircuitState.CLOSED
