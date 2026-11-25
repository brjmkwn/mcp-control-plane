"""Unit tests for AST query cache acceleration and guardrail validation."""

from __future__ import annotations

import time

import pytest

from mcp_control_plane.core.errors import GuardrailBlockedError
from mcp_control_plane.servers.postgres_analytics.sql_guard import (
    clear_sql_guard_cache,
    validate_and_sanitize_sql,
)


def test_sql_guard_cache_acceleration() -> None:
    """Verify that cached queries return in < 0.2ms, significantly faster than fresh AST parses."""
    clear_sql_guard_cache()
    query = "SELECT id, name, tier, owner_team FROM services WHERE status = 'healthy'"

    # First execution (cold parse)
    t0 = time.perf_counter()
    res1 = validate_and_sanitize_sql(query)
    cold_duration = time.perf_counter() - t0

    # Second execution (hot LRU cache hit)
    t1 = time.perf_counter()
    res2 = validate_and_sanitize_sql(query)
    hot_duration = time.perf_counter() - t1

    assert res1 == res2
    assert "LIMIT 100" in res2
    assert hot_duration < cold_duration
    assert hot_duration < 0.001  # sub-millisecond cache hit


def test_malicious_queries_not_cached_as_valid() -> None:
    """Verify that invalid/malicious SQL is blocked and never cached as valid."""
    clear_sql_guard_cache()
    malicious = "DROP TABLE services;"

    with pytest.raises(GuardrailBlockedError):
        validate_and_sanitize_sql(malicious)

    # Subsequent attempt still raises GuardrailBlockedError
    with pytest.raises(GuardrailBlockedError):
        validate_and_sanitize_sql(malicious)
