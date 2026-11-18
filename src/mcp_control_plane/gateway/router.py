"""FastAPI Router for Streamable HTTP MCP endpoint (POST /mcp), GET /health, and GET /metrics."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from mcp_control_plane.config import Settings, get_settings
from mcp_control_plane.core.errors import MCPControlPlaneError
from mcp_control_plane.core.metrics import get_latest_metrics
from mcp_control_plane.core.models import JsonRpcRequest, JsonRpcResponse
from mcp_control_plane.gateway.auth import authenticate_bearer, validate_origin
from mcp_control_plane.gateway.registry import ToolRegistry

router = APIRouter()


def create_jsonrpc_error_response(
    req_id: str | int | None,
    err: MCPControlPlaneError,
) -> JSONResponse:
    """Serialize MCPControlPlaneError into JSON-RPC 2.0 error format."""
    envelope = err.to_envelope()
    rpc_response = JsonRpcResponse(
        id=req_id,
        error={
            "code": -32000,
            "message": err.message,
            "data": envelope["error"],
        },
    )
    return JSONResponse(
        content=rpc_response.model_dump(),
        status_code=err.status_code,
    )


async def sse_event_stream(response_data: dict[str, Any]) -> AsyncIterator[str]:
    """Yield a single SSE formatted message chunk."""
    payload = json.dumps(response_data)
    yield f"event: message\ndata: {payload}\n\n"


@router.post("/mcp")
async def handle_mcp_endpoint(
    request: Request,
    origin: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    accept: str | None = Header(default=None),
) -> Response:
    """Unified Streamable HTTP endpoint for JSON-RPC 2.0 MCP messages."""
    settings: Settings = (
        request.app.state.settings if hasattr(request.app.state, "settings") else get_settings()
    )
    registry: ToolRegistry = request.app.state.registry

    # 1. Transport Layer: Validate Origin header (DNS-rebinding prevention)
    try:
        validate_origin(origin, settings.mcp_allowed_origins)
    except MCPControlPlaneError as err:
        return create_jsonrpc_error_response(None, err)

    # 2. Authentication Layer: Scoped Bearer RBAC
    token_store = settings.get_token_map()
    try:
        principal = authenticate_bearer(authorization, token_store)
    except MCPControlPlaneError as err:
        return create_jsonrpc_error_response(None, err)

    # 3. Parse JSON-RPC 2.0 payload
    try:
        body = await request.json()
        rpc_request = JsonRpcRequest.model_validate(body)
    except Exception as parse_err:
        from mcp_control_plane.core.errors import ValidationFailedError

        err = ValidationFailedError(f"Invalid JSON-RPC 2.0 payload: {parse_err}")
        return create_jsonrpc_error_response(None, err)

    req_id = rpc_request.id
    method = rpc_request.method
    params = rpc_request.params or {}

    try:
        # Route MCP protocol methods
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "mcp-control-plane",
                    "version": "1.0.0",
                },
            }
        elif method == "tools/list":
            tools_list = registry.list_tools_for_principal(principal)
            result = {"tools": tools_list}
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            tool_res = await registry.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                principal=principal,
                request_id=str(req_id) if req_id else None,
            )
            result = {
                "content": tool_res.content,
                "isError": tool_res.is_error,
                "metadata": tool_res.metadata,
            }
        else:
            from mcp_control_plane.core.errors import ValidationFailedError

            raise ValidationFailedError(
                f"Unsupported JSON-RPC method: '{method}'. Supported methods: 'initialize', 'tools/list', 'tools/call'."
            )

        rpc_response = JsonRpcResponse(id=req_id, result=result).model_dump()

        # Handle Streamable HTTP SSE streaming if Accept: text/event-stream
        if accept and "text/event-stream" in accept:
            return StreamingResponse(
                sse_event_stream(rpc_response),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        return JSONResponse(content=rpc_response, status_code=200)

    except MCPControlPlaneError as err:
        return create_jsonrpc_error_response(req_id, err)


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Readiness probe checking gateway and attached dependencies."""
    settings: Settings = (
        request.app.state.settings if hasattr(request.app.state, "settings") else get_settings()
    )

    db_status = "configured" if settings.database_url else "standalone_mode"
    redis_status = "configured" if settings.redis_url else "in_memory_mode"

    return JSONResponse(
        content={
            "status": "healthy",
            "gateway": "online",
            "dependencies": {
                "database": db_status,
                "redis": redis_status,
                "github_provider": settings.github_provider_mode,
            },
        },
        status_code=200,
    )


@router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus telemetry scrape endpoint."""
    content, content_type = get_latest_metrics()
    return Response(content=content, media_type=content_type)
