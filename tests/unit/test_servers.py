"""Unit tests for all 13 tools across the three enterprise MCP servers."""

from __future__ import annotations

import pytest

from mcp_control_plane.config import ClientPrincipal
from mcp_control_plane.gateway.confirmation import ConfirmationGate
from mcp_control_plane.gateway.registry import ToolRegistry
from mcp_control_plane.servers.github_triage import (
    DeterministicMockGitHubProvider,
    register_github_triage_tools,
)
from mcp_control_plane.servers.observability import (
    DeterministicTelemetryStore,
    register_observability_tools,
)
from mcp_control_plane.servers.postgres_analytics import (
    SQLiteFallbackAnalyticsStore,
    register_postgres_analytics_tools,
)


@pytest.fixture
def enterprise_registry() -> ToolRegistry:
    gate = ConfirmationGate(secret="test-secret-at-least-32-chars-long!")
    registry = ToolRegistry(confirmation_gate=gate)

    db_store = SQLiteFallbackAnalyticsStore()
    gh_provider = DeterministicMockGitHubProvider()
    telemetry_provider = DeterministicTelemetryStore()

    register_postgres_analytics_tools(registry, db_store)
    register_github_triage_tools(registry, gh_provider)
    register_observability_tools(registry, telemetry_provider)

    return registry


@pytest.fixture
def admin_principal() -> ClientPrincipal:
    return ClientPrincipal(
        client_id="admin-tester",
        scopes=["admin", "read:db", "read:github", "write:github", "read:ops"],
    )


# ----------------------------------------------------
# Server 1: PostgreSQL & Vector Analytics Tests
# ----------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_list_tables(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool("list_tables", {}, admin_principal)
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["total"] >= 4
    table_names = [t["table_name"] for t in data["tables"]]
    assert "services" in table_names
    assert "deployments" in table_names


@pytest.mark.asyncio
async def test_postgres_describe_schema(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "describe_schema", {"table_name": "services"}, admin_principal
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    columns = [c["column_name"] for c in data["columns"]]
    assert "name" in columns
    assert "tier" in columns


@pytest.mark.asyncio
async def test_postgres_run_readonly_query(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "run_readonly_query",
        {"query": "SELECT name, status FROM services WHERE status = 'healthy'"},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["count"] >= 1
    assert any(r["name"] == "auth-service" for r in data["rows"])


@pytest.mark.asyncio
async def test_postgres_hybrid_search_docs(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "hybrid_search_docs",
        {"query": "payment gateway timeout remediation"},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert len(data["results"]) > 0
    assert "Payment Gateway" in data["results"][0]["title"]


# ----------------------------------------------------
# Server 2: GitHub CI & Incident Triage Tests
# ----------------------------------------------------


@pytest.mark.asyncio
async def test_github_get_failed_workflow_logs(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "get_failed_workflow_logs",
        {"owner": "acme", "repo": "backend", "run_id": 12345},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["conclusion"] == "failure"
    assert "AssertionError" in data["error_excerpt"]


@pytest.mark.asyncio
async def test_github_get_commit_diff(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "get_commit_diff",
        {"owner": "acme", "repo": "backend", "commit_sha": "a1b2c3d4e5"},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["commit_sha"] == "a1b2c3d4e5"
    assert len(data["files_changed"]) > 0


@pytest.mark.asyncio
async def test_github_list_open_incidents(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "list_open_incidents",
        {"owner": "acme", "repo": "backend", "label": "incident"},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["count"] >= 1
    assert any("Incident" in inc["title"] for inc in data["incidents"])


@pytest.mark.asyncio
async def test_github_draft_triage_issue_2_step_gate(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    args = {
        "owner": "acme",
        "repo": "backend",
        "title": "Production latency spike investigation",
        "body": "Root cause analysis indicates payment gateway saturation.",
        "labels": ["incident", "p1"],
    }
    # Step 1: Call without confirmation_token -> Returns confirmation_required prompt
    res1 = await enterprise_registry.execute_tool("draft_triage_issue", args, admin_principal)
    assert res1.metadata.get("confirmation_required") is True
    import json

    data1 = json.loads(res1.content[0]["text"])
    assert data1["code"] == "CONFIRMATION_REQUIRED"
    token = data1["details"]["confirmation_token"]
    assert token is not None

    # Step 2: Call with valid confirmation_token -> Executes mutating action
    args_with_token = {**args, "confirmation_token": token}
    res2 = await enterprise_registry.execute_tool(
        "draft_triage_issue", args_with_token, admin_principal
    )
    assert not res2.is_error
    data2 = json.loads(res2.content[0]["text"])
    assert data2["title"] == args["title"]
    assert data2["issue_number"] > 200


@pytest.mark.asyncio
async def test_github_post_pr_triage_comment_2_step_gate(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    args = {
        "owner": "acme",
        "repo": "backend",
        "pr_number": 42,
        "comment": "Recommended fix: Increase pool timeout and enable retry circuit breaker.",
    }
    # Step 1: Prompt
    res1 = await enterprise_registry.execute_tool("post_pr_triage_comment", args, admin_principal)
    assert res1.metadata.get("confirmation_required") is True
    import json

    token = json.loads(res1.content[0]["text"])["details"]["confirmation_token"]

    # Step 2: Confirmation
    args_confirmed = {**args, "confirmation_token": token}
    res2 = await enterprise_registry.execute_tool(
        "post_pr_triage_comment", args_confirmed, admin_principal
    )
    assert not res2.is_error
    data2 = json.loads(res2.content[0]["text"])
    assert data2["pr_number"] == 42


# ----------------------------------------------------
# Server 3: Observability & Telemetry Tests
# ----------------------------------------------------


@pytest.mark.asyncio
async def test_observability_query_latency_anomalies(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "query_latency_anomalies",
        {"threshold_ms": 200.0, "lookback_minutes": 15},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["count"] >= 1
    anomalous_services = [a["service"] for a in data["anomalies"]]
    assert "order-fulfillment" in anomalous_services


@pytest.mark.asyncio
async def test_observability_get_error_breakdown(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "get_error_breakdown",
        {"service": "order-fulfillment"},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["count"] >= 1
    assert any(e["error_code"] == "504_GATEWAY_TIMEOUT" for e in data["errors"])


@pytest.mark.asyncio
async def test_observability_search_structured_logs(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "search_structured_logs",
        {"severity": "ERROR", "limit": 10},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["count"] >= 1
    assert all(log["severity"] == "ERROR" for log in data["logs"])


@pytest.mark.asyncio
async def test_observability_diagnose_service_health(
    enterprise_registry: ToolRegistry, admin_principal: ClientPrincipal
) -> None:
    res = await enterprise_registry.execute_tool(
        "diagnose_service_health",
        {"service_name": "order-fulfillment"},
        admin_principal,
    )
    assert not res.is_error
    import json

    data = json.loads(res.content[0]["text"])
    assert data["status"] in ("CRITICAL", "DEGRADED")
    assert len(data["recommended_actions"]) > 0
