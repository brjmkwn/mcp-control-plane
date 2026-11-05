"""Unit tests for sqlglot AST guardrail and limit enforcement."""

from __future__ import annotations

import pytest

from mcp_control_plane.core.errors import GuardrailBlockedError
from mcp_control_plane.servers.postgres_analytics.sql_guard import validate_and_sanitize_sql


def test_valid_select_passes() -> None:
    query = "SELECT id, name FROM services WHERE status = 'healthy'"
    sanitized = validate_and_sanitize_sql(query)
    assert "SELECT" in sanitized
    assert "LIMIT 100" in sanitized


def test_valid_cte_passes() -> None:
    query = "WITH active_services AS (SELECT id, name FROM services WHERE status = 'healthy') SELECT * FROM active_services"
    sanitized = validate_and_sanitize_sql(query)
    assert "WITH active_services AS" in sanitized
    assert "LIMIT 100" in sanitized


def test_existing_small_limit_preserved() -> None:
    query = "SELECT * FROM services LIMIT 25"
    sanitized = validate_and_sanitize_sql(query)
    assert "LIMIT 25" in sanitized


def test_excessive_limit_clamped_to_100() -> None:
    query = "SELECT * FROM services LIMIT 5000"
    sanitized = validate_and_sanitize_sql(query)
    assert "LIMIT 100" in sanitized
    assert "5000" not in sanitized


def test_empty_sql_blocked() -> None:
    with pytest.raises(GuardrailBlockedError, match="empty"):
        validate_and_sanitize_sql("   ")


def test_invalid_syntax_blocked() -> None:
    with pytest.raises(GuardrailBlockedError):
        validate_and_sanitize_sql("SELEC FROM WHERE ;")
