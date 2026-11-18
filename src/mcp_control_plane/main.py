"""FastAPI Application factory and CLI entrypoint for HTTP and stdio transports."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mcp_control_plane.config import ClientPrincipal, Settings, get_settings
from mcp_control_plane.core.logging import get_logger, setup_logging
from mcp_control_plane.core.models import JsonRpcRequest, JsonRpcResponse
from mcp_control_plane.gateway.confirmation import (
    ConfirmationGate,
    InMemoryNonceStore,
    RedisNonceStore,
)
from mcp_control_plane.gateway.rate_limit import (
    InMemorySlidingWindowRateLimiter,
    RedisSlidingWindowRateLimiter,
)
from mcp_control_plane.gateway.registry import ToolRegistry
from mcp_control_plane.gateway.router import router
from mcp_control_plane.servers.github_triage import (
    DeterministicMockGitHubProvider,
    LiveGitHubProvider,
    register_github_triage_tools,
)
from mcp_control_plane.servers.observability import (
    DeterministicTelemetryStore,
    LivePrometheusTelemetryProvider,
    register_observability_tools,
)
from mcp_control_plane.servers.postgres_analytics import (
    PostgresAnalyticsStore,
    SQLiteFallbackAnalyticsStore,
    register_postgres_analytics_tools,
)

logger = get_logger("mcp_main")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the production FastAPI MCP gateway with all 13 tools wired."""
    current_settings = settings or get_settings()
    setup_logging()

    # Determine backing stores for rate limiting and confirmation nonces
    redis_client = None
    if current_settings.redis_url and not current_settings.test_mode:
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(
                current_settings.redis_url,
                decode_responses=True,
            )
        except Exception as e:
            logger.warning("redis_connection_failed_fallback_in_memory", error=str(e))

    if redis_client:
        rate_limiter = RedisSlidingWindowRateLimiter(redis_client)
        nonce_store = RedisNonceStore(redis_client)
    else:
        rate_limiter = InMemorySlidingWindowRateLimiter()
        nonce_store = InMemoryNonceStore()

    confirmation_gate = ConfirmationGate(
        secret=current_settings.mcp_confirmation_secret,
        ttl_seconds=current_settings.confirmation_ttl_seconds,
        nonce_store=nonce_store,
    )

    registry = ToolRegistry(
        rate_limiter=rate_limiter,
        confirmation_gate=confirmation_gate,
    )

    # 1. Wire Server 1: PostgreSQL & Vector Analytics (4 tools)
    if current_settings.database_url and not current_settings.test_mode:
        db_store = PostgresAnalyticsStore(current_settings.database_url)
    else:
        db_store = SQLiteFallbackAnalyticsStore()
    register_postgres_analytics_tools(registry, db_store)

    # 2. Wire Server 2: GitHub CI & Incident Triage (5 tools)
    if current_settings.github_provider_mode == "live" and current_settings.github_token:
        gh_provider = LiveGitHubProvider(current_settings.github_token)
    else:
        gh_provider = DeterministicMockGitHubProvider()
    register_github_triage_tools(registry, gh_provider)

    # 3. Wire Server 3: Service Observability & Telemetry (4 tools)
    if current_settings.prometheus_url and not current_settings.test_mode:
        telemetry_provider = LivePrometheusTelemetryProvider(current_settings.prometheus_url)
    else:
        telemetry_provider = DeterministicTelemetryStore()
    register_observability_tools(registry, telemetry_provider)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("mcp_gateway_starting", transport="http", tools_registered=len(registry.tools))
        yield
        if redis_client:
            await redis_client.aclose()
        logger.info("mcp_gateway_shutdown_complete")

    app = FastAPI(
        title="mcp-control-plane",
        description="Zero-Trust Production MCP Gateway & Enterprise Multi-Server Suite",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.settings = current_settings
    app.state.registry = registry
    app.state.db_store = db_store
    app.state.gh_provider = gh_provider
    app.state.telemetry_provider = telemetry_provider
    app.include_router(router)

    return app


# Alias build_app to create_app
build_app = create_app


async def run_stdio_transport(registry: ToolRegistry) -> None:
    """Run local stdio JSON-RPC transport for desktop MCP hosts (Claude Desktop/Cursor)."""
    logger.info("mcp_gateway_starting_stdio", tools_registered=len(registry.tools))
    local_principal = ClientPrincipal(
        client_id="stdio-local-user",
        scopes=["admin", "read:db", "read:github", "write:github", "read:ops"],
    )

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line_bytes = await reader.readline()
        if not line_bytes:
            break

        line = line_bytes.decode("utf-8").strip()
        if not line:
            continue

        try:
            body = json.loads(line)
            req = JsonRpcRequest.model_validate(body)
            req_id = req.id
            method = req.method
            params = req.params or {}

            if method == "initialize":
                res = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-control-plane", "version": "1.0.0"},
                }
            elif method == "tools/list":
                res = {"tools": registry.list_tools_for_principal(local_principal)}
            elif method == "tools/call":
                tool_res = await registry.execute_tool(
                    tool_name=params.get("name", ""),
                    arguments=params.get("arguments", {}),
                    principal=local_principal,
                    request_id=str(req_id) if req_id else None,
                )
                res = {"content": tool_res.content, "isError": tool_res.is_error}
            else:
                res = {"error": f"Unknown method: {method}"}

            rpc_out = JsonRpcResponse(id=req_id, result=res).model_dump()
            sys.stdout.write(json.dumps(rpc_out) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_out = JsonRpcResponse(
                id=None,
                error={"code": -32000, "message": str(e)},
            ).model_dump()
            sys.stdout.write(json.dumps(err_out) + "\n")
            sys.stdout.flush()


def cli_main() -> None:
    """Command-line interface entrypoint."""
    parser = argparse.ArgumentParser(description="mcp-control-plane Gateway")
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default="http",
        help="Transport mode: http (production FastAPI) or stdio (local MCP client)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host to bind")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port to bind")
    args = parser.parse_args()

    settings = get_settings()
    app = create_app(settings)

    if args.transport == "http":
        import uvicorn

        uvicorn.run(app, host=args.host or settings.mcp_host, port=args.port or settings.mcp_port)
    else:
        asyncio.run(run_stdio_transport(app.state.registry))


if __name__ == "__main__":
    cli_main()
