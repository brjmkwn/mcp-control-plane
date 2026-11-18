"""Integration tests for Streamable HTTP MCP (POST /mcp), JSON-RPC 2.0, SSE, and endpoints."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from mcp_control_plane.config import Settings
from mcp_control_plane.main import create_app


@pytest.fixture
def full_app():
    settings = Settings(
        MCP_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000",
        MCP_TOKENS_JSON=(
            '{"admin-token":{"client_id":"admin-user","scopes":["admin","read:db","read:github","write:github","read:ops"]},'
            '"db-token":{"client_id":"db-user","scopes":["read:db"]},'
            '"gh-token":{"client_id":"gh-user","scopes":["read:github","write:github"]},'
            '"ops-token":{"client_id":"ops-user","scopes":["read:ops"]}}'
        ),
        MCP_CONFIRMATION_SECRET="a-very-long-and-secure-32-byte-secret-key!",
        MCP_TEST_MODE=True,
    )
    return create_app(settings)


@pytest.fixture
async def client(full_app) -> AsyncClient:
    transport = ASGITransport(app=full_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_jsonrpc_initialize(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer admin-token"}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    response = await client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert data["result"]["serverInfo"]["name"] == "mcp-control-plane"


@pytest.mark.asyncio
async def test_tools_list_total_13_tools(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer admin-token"}
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    response = await client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert len(tools) == 13

    tool_names = [t["name"] for t in tools]
    # Check Server 1 tools
    assert "list_tables" in tool_names
    assert "describe_schema" in tool_names
    assert "run_readonly_query" in tool_names
    assert "hybrid_search_docs" in tool_names
    # Check Server 2 tools
    assert "get_failed_workflow_logs" in tool_names
    assert "get_commit_diff" in tool_names
    assert "list_open_incidents" in tool_names
    assert "draft_triage_issue" in tool_names
    assert "post_pr_triage_comment" in tool_names
    # Check Server 3 tools
    assert "query_latency_anomalies" in tool_names
    assert "get_error_breakdown" in tool_names
    assert "search_structured_logs" in tool_names
    assert "diagnose_service_health" in tool_names


@pytest.mark.asyncio
async def test_streamable_http_sse_mode(client: AsyncClient) -> None:
    headers = {
        "Authorization": "Bearer admin-token",
        "Accept": "text/event-stream",
    }
    payload = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
    response = await client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body_text = response.text
    assert body_text.startswith("event: message\ndata: ")
    # Parse json inside sse data
    json_str = body_text.split("data: ")[1].strip()
    data = json.loads(json_str)
    assert len(data["result"]["tools"]) == 13


@pytest.mark.asyncio
async def test_e2e_two_step_confirmation_flow(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer gh-token"}

    # Step 1: Call write tool without token
    step1_payload = {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "draft_triage_issue",
            "arguments": {
                "owner": "corp",
                "repo": "monorepo",
                "title": "Sev-1 Outage in Auth",
                "body": "Token refresh failures across all regions",
            },
        },
    }
    res1 = await client.post("/mcp", headers=headers, json=step1_payload)
    assert res1.status_code == 200
    data1 = res1.json()["result"]
    assert data1["metadata"]["confirmation_required"] is True
    error_content = json.loads(data1["content"][0]["text"])
    token = error_content["details"]["confirmation_token"]
    assert token is not None

    # Step 2: Call write tool with received confirmation token
    step2_payload = {
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "draft_triage_issue",
            "arguments": {
                "owner": "corp",
                "repo": "monorepo",
                "title": "Sev-1 Outage in Auth",
                "body": "Token refresh failures across all regions",
                "confirmation_token": token,
            },
        },
    }
    res2 = await client.post("/mcp", headers=headers, json=step2_payload)
    assert res2.status_code == 200
    data2 = res2.json()["result"]
    assert not data2["isError"]
    created_issue = json.loads(data2["content"][0]["text"])
    assert created_issue["title"] == "Sev-1 Outage in Auth"

    # Step 3: Replay attack (reuse consumed token) -> Fails closed
    res3 = await client.post("/mcp", headers=headers, json=step2_payload)
    assert res3.status_code == 400
    err3 = res3.json()["error"]["data"]
    assert err3["code"] == "VALIDATION_FAILED"
    assert "Replay rejected" in err3["message"]


@pytest.mark.asyncio
async def test_health_and_metrics_endpoints(client: AsyncClient) -> None:
    # 1. Health endpoint
    health_res = await client.get("/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert health_data["status"] == "healthy"
    assert health_data["gateway"] == "online"

    # 2. Metrics endpoint
    metrics_res = await client.get("/metrics")
    assert metrics_res.status_code == 200
    metrics_text = metrics_res.text
    assert "mcp_tool_calls_total" in metrics_text
    assert "mcp_tool_duration_seconds" in metrics_text
    assert "mcp_guardrail_blocks_total" in metrics_text
