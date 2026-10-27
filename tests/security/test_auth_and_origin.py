"""Security tests for Origin validation and Scoped Bearer Token RBAC."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_disallowed_origin_blocked(async_client: AsyncClient) -> None:
    """Requests with non-allowlisted Origin headers must fail closed with 403."""
    headers = {
        "Origin": "https://evil.attacker.com",
        "Authorization": "Bearer admin-token",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }
    response = await async_client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["data"]["code"] == "ORIGIN_DENIED"


@pytest.mark.asyncio
async def test_allowed_origin_accepted(async_client: AsyncClient) -> None:
    """Requests with allowlisted Origin headers must be processed."""
    headers = {
        "Origin": "http://localhost:3000",
        "Authorization": "Bearer admin-token",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }
    response = await async_client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 200
    assert "tools" in response.json()["result"]


@pytest.mark.asyncio
async def test_missing_origin_allowed_for_cli(async_client: AsyncClient) -> None:
    """Non-browser CLI/server clients sending no Origin header must be accepted."""
    headers = {
        "Authorization": "Bearer admin-token",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
    }
    response = await async_client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2024-11-05"


@pytest.mark.asyncio
async def test_unauthenticated_request_blocked(async_client: AsyncClient) -> None:
    """Requests with missing or malformed Authorization header must fail closed with 401."""
    # Missing header
    res1 = await async_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert res1.status_code == 401
    assert res1.json()["error"]["data"]["code"] == "UNAUTHENTICATED"

    # Malformed header
    res2 = await async_client.post(
        "/mcp",
        headers={"Authorization": "Basic 12345"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert res2.status_code == 401

    # Invalid token
    res3 = await async_client.post(
        "/mcp",
        headers={"Authorization": "Bearer non-existent-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert res3.status_code == 401


@pytest.mark.asyncio
async def test_scoped_rbac_denied_for_insufficient_privileges(async_client: AsyncClient) -> None:
    """Client with read:db token attempting to call write:github tool must return 403."""
    headers = {"Authorization": "Bearer db-token"}
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "test_write_tool",
            "arguments": {"action": "delete", "target": "repo"},
        },
    }
    response = await async_client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["data"]["code"] == "FORBIDDEN_SCOPE"
    assert data["error"]["data"]["details"]["required_scope"] == "write:github"


@pytest.mark.asyncio
async def test_tools_list_filters_by_client_scope(async_client: AsyncClient) -> None:
    """tools/list returns only tools permitted by the client's scopes."""
    # db-token has only read:db
    res_db = await async_client.post(
        "/mcp",
        headers={"Authorization": "Bearer db-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert res_db.status_code == 200
    db_tools = [t["name"] for t in res_db.json()["result"]["tools"]]
    assert "test_read_tool" in db_tools
    assert "test_write_tool" not in db_tools

    # admin-token sees all tools
    res_admin = await async_client.post(
        "/mcp",
        headers={"Authorization": "Bearer admin-token"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert res_admin.status_code == 200
    admin_tools = [t["name"] for t in res_admin.json()["result"]["tools"]]
    assert "test_read_tool" in admin_tools
    assert "test_write_tool" in admin_tools
