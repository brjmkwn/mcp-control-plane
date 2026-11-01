"""Async Circuit Breaker state machine (CLOSED/OPEN/HALF_OPEN)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from mcp_control_plane.core.errors import CircuitOpenError
from mcp_control_plane.core.metrics import set_circuit_breaker_state


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


STATE_TO_GAUGE = {
    CircuitState.CLOSED: 0,
    CircuitState.OPEN: 1,
    CircuitState.HALF_OPEN: 2,
}


class CircuitBreaker:
    """Per-upstream circuit breaker protecting against cascading failures."""

    def __init__(
        self,
        upstream_name: str,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
    ) -> None:
        self.upstream_name = upstream_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()
        set_circuit_breaker_state(self.upstream_name, STATE_TO_GAUGE[self._state])

    @property
    def state(self) -> CircuitState:
        return self._state

    def _update_state(self, new_state: CircuitState) -> None:
        if self._state != new_state:
            self._state = new_state
            set_circuit_breaker_state(self.upstream_name, STATE_TO_GAUGE[new_state])

    async def before_call(self) -> None:
        """Check breaker state before allowing request to proceed."""
        async with self._lock:
            now = time.time()
            if self._state == CircuitState.OPEN:
                elapsed = now - self._opened_at
                if elapsed >= self.recovery_timeout_seconds:
                    self._update_state(CircuitState.HALF_OPEN)
                else:
                    cooldown_remaining = self.recovery_timeout_seconds - elapsed
                    raise CircuitOpenError(
                        upstream_name=self.upstream_name,
                        cooldown_remaining_seconds=cooldown_remaining,
                    )

    async def record_success(self) -> None:
        """Record upstream request success."""
        async with self._lock:
            self._consecutive_failures = 0
            if self._state != CircuitState.CLOSED:
                self._update_state(CircuitState.CLOSED)

    async def record_failure(self) -> None:
        """Record upstream request failure and trip breaker if threshold exceeded."""
        async with self._lock:
            self._consecutive_failures += 1
            if (
                self._state == CircuitState.HALF_OPEN
                or self._consecutive_failures >= self.failure_threshold
            ):
                self._opened_at = time.time()
                self._update_state(CircuitState.OPEN)

    async def execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Wrap an async upstream function with circuit breaker protection."""
        await self.before_call()
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception:
            await self.record_failure()
            raise
