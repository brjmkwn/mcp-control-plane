"""Pytest shared fixtures and test configuration."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from mcp_control_plane.config import Settings, set_settings
from mcp_control_plane.core.models import ToolResult, ToolSpec
from mcp_control_plane.main import create_app


class SimpleQueryInput(BaseModel):
    query: str


class SimpleWriteInput(BaseModel):
    action: str
    target: str


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing deterministic test configuration."""
    settings = Settings(
        MCP_ALLOWED_ORIGINS="http://localhost:3000,https://app.example.com",
        MCP_TOKENS_JSON=(
            '{"admin-token":{"client_id":"admin-user","scopes":["admin","read:db","read:github","write:github","read:ops"]},'
            '"db-token":{"client_id":"db-user","scopes":["read:db"]},'
            '"gh-token":{"client_id":"gh-user","scopes":["read:github","write:github"]},'
            '"ops-token":{"client_id":"ops-user","scopes":["read:ops"]}}'
        ),
        MCP_CONFIRMATION_SECRET="test-secret-key-that-is-at-least-32-bytes-long!",
        MCP_TEST_MODE=True,
    )
    set_settings(settings)
    return settings


@pytest.fixture
def test_app(test_settings: Settings):
    """Fixture creating configured FastAPI app with registered dummy tools."""
    app = create_app(test_settings)
    registry = app.state.registry

    # Register read tool
    async def sample_read_handler(input_data: SimpleQueryInput) -> ToolResult:
        return ToolResult.text(f"Read executed: {input_data.query}")

    registry.register(
        ToolSpec(
            name="test_read_tool",
            server="test_server",
            description="Test read tool",
            input_model=SimpleQueryInput,
            handler=sample_read_handler,
            required_scope="read:db",
            mutating=False,
            rate_limit_rpm=60,
            max_concurrency=10,
            upstream_name="test_upstream",
        )
    )

    # Register mutating tool requiring confirmation
    async def sample_write_handler(input_data: SimpleWriteInput) -> ToolResult:
        return ToolResult.text(f"Write executed: {input_data.action} on {input_data.target}")

    registry.register(
        ToolSpec(
            name="test_write_tool",
            server="test_server",
            description="Test write mutating tool",
            input_model=SimpleWriteInput,
            handler=sample_write_handler,
            required_scope="write:github",
            mutating=True,
            rate_limit_rpm=30,
            max_concurrency=5,
            upstream_name="test_upstream",
        )
    )

    return app


@pytest.fixture
async def async_client(test_app) -> AsyncClient:
    """Async HTTP client targeting the in-memory ASGI test application."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
