"""Unit tests for cross-platform local stdio transport."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from mcp_control_plane.core.models import ToolResult, ToolSpec
from mcp_control_plane.gateway.registry import ToolRegistry
from mcp_control_plane.main import run_stdio_transport


class EchoInput(BaseModel):
    msg: str


@pytest.mark.asyncio
async def test_stdio_transport_lifecycle() -> None:
    """Verify initialize, tools/list, and tools/call over stdio JSON-RPC stream."""
    registry = ToolRegistry()

    # Register sample echo tool
    async def echo_handler(args: EchoInput) -> ToolResult:
        return ToolResult.text(f"echo: {args.msg}")

    spec = ToolSpec(
        name="test_echo",
        server="test_server",
        description="Echo input message",
        input_model=EchoInput,
        handler=echo_handler,
        required_scope="read:ops",
        upstream_name="test_upstream",
    )
    registry.register(spec)

    # Prepare simulated stdin input
    rpc_requests = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "test_echo", "arguments": {"msg": "hello-stdio"}},
            }
        ),
    ]
    simulated_stdin = "\n".join(rpc_requests) + "\n"

    fake_stdin = io.StringIO(simulated_stdin)
    fake_stdout = io.StringIO()

    with patch("sys.stdin", fake_stdin), patch("sys.stdout", fake_stdout):
        # Run transport until EOF is hit
        await run_stdio_transport(registry)

    output_lines = [line.strip() for line in fake_stdout.getvalue().splitlines() if line.strip()]
    assert len(output_lines) == 3

    # 1. Initialize response
    init_res = json.loads(output_lines[0])
    assert init_res["id"] == 1
    assert init_res["result"]["serverInfo"]["name"] == "mcp-control-plane"

    # 2. Tools list response
    list_res = json.loads(output_lines[1])
    assert list_res["id"] == 2
    tool_names = [t["name"] for t in list_res["result"]["tools"]]
    assert "test_echo" in tool_names

    # 3. Tool call response
    call_res = json.loads(output_lines[2])
    assert call_res["id"] == 3
    assert not call_res["result"]["isError"]
    assert "echo: hello-stdio" in call_res["result"]["content"][0]["text"]
