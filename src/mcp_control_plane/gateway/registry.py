"""Unified MCP tool registry and ordered 7-step middleware execution pipeline."""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from typing import Any

from pydantic import ValidationError

from mcp_control_plane.config import ClientPrincipal
from mcp_control_plane.core.errors import (
    ConcurrencyLimitError,
    ConfirmationRequiredError,
    MCPControlPlaneError,
    ValidationFailedError,
)
from mcp_control_plane.core.logging import get_logger
from mcp_control_plane.core.metrics import (
    record_guardrail_block,
    record_tool_call,
)
from mcp_control_plane.core.models import (
    AuditRecord,
    ToolResult,
    ToolSpec,
)
from mcp_control_plane.gateway.auth import require_scope
from mcp_control_plane.gateway.circuit_breaker import CircuitBreaker
from mcp_control_plane.gateway.confirmation import ConfirmationGate
from mcp_control_plane.gateway.rate_limit import InMemorySlidingWindowRateLimiter, RateLimiter

logger = get_logger("mcp_gateway.registry")


class ToolRegistry:
    """Manages MCP tool definitions, per-tool concurrency semaphores, and execution pipeline."""

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        confirmation_gate: ConfirmationGate | None = None,
    ) -> None:
        self.tools: dict[str, ToolSpec] = {}
        self.semaphores: dict[str, asyncio.Semaphore] = {}
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
        self.rate_limiter = rate_limiter or InMemorySlidingWindowRateLimiter()
        self.confirmation_gate = confirmation_gate or ConfirmationGate(
            secret="default-secret-32-chars-long-minimum-gate"
        )

    def register(self, tool: ToolSpec) -> None:
        """Register a new tool specification and initialize its semaphore and circuit breaker."""
        self.tools[tool.name] = tool
        self.semaphores[tool.name] = asyncio.Semaphore(tool.max_concurrency)
        if tool.upstream_name not in self.circuit_breakers:
            self.circuit_breakers[tool.upstream_name] = CircuitBreaker(tool.upstream_name)

    def get_tool(self, name: str) -> ToolSpec | None:
        """Fetch tool specification by name."""
        return self.tools.get(name)

    def list_tools_for_principal(self, principal: ClientPrincipal) -> list[dict[str, Any]]:
        """Return MCP tools/list schemas visible to the authenticated principal's scopes."""
        is_admin = "admin" in principal.scopes
        visible = []
        for tool in self.tools.values():
            if is_admin or tool.required_scope in principal.scopes:
                visible.append(tool.to_mcp_schema())
        return visible

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        principal: ClientPrincipal,
        request_id: str | None = None,
    ) -> ToolResult:
        """Execute ordered 7-step middleware pipeline for a tool call."""
        req_id = request_id or uuid.uuid4().hex
        start_time = time.perf_counter()
        tool = self.tools.get(tool_name)

        if not tool:
            record_guardrail_block("unknown_tool", tool=tool_name)
            raise ValidationFailedError(
                f"Unknown tool: '{tool_name}'. Call 'tools/list' to view available tools.",
                details={"requested_tool": tool_name},
            )

        error_code: str | None = None
        status: str = "success"

        try:
            # Step 1: Scoped RBAC check
            require_scope(principal, tool.required_scope, tool_name=tool.name)

            # Step 2: Per-tool Sliding-Window Rate Limiter
            allowed, _, retry_after_ms = await self.rate_limiter.check_limit(
                client_id=principal.client_id,
                tool_name=tool.name,
                limit_rpm=tool.rate_limit_rpm,
            )
            if not allowed:
                from mcp_control_plane.core.errors import RateLimitExceededError

                record_guardrail_block("rate_limited", tool=tool.name)
                raise RateLimitExceededError(
                    retry_after_ms=retry_after_ms,
                    limit_rpm=tool.rate_limit_rpm,
                    tool_name=tool.name,
                )

            # Step 3: Concurrency Semaphore check (non-blocking check for instant 429)
            semaphore = self.semaphores[tool.name]
            if semaphore._value <= 0:
                record_guardrail_block("concurrency_limit", tool=tool.name)
                raise ConcurrencyLimitError(
                    tool_name=tool.name, max_concurrency=tool.max_concurrency
                )

            # Acquire semaphore for actual execution
            async with semaphore:
                # Step 4: Pydantic Schema Validation
                try:
                    # Filter out gateway-injected confirmation_token from model validation if not in fields
                    input_args = {k: v for k, v in arguments.items() if k != "confirmation_token"}
                    validated_input = tool.input_model.model_validate(input_args)
                except ValidationError as val_err:
                    record_guardrail_block("validation_failed", tool=tool.name)
                    raise ValidationFailedError(
                        f"Argument validation failed for tool '{tool.name}'.",
                        details={"errors": val_err.errors()},
                    ) from val_err

                # Step 5: 2-Step HMAC Confirmation Gate
                if tool.mutating:
                    await self.confirmation_gate.check_or_prompt_confirmation(
                        client_id=principal.client_id,
                        tool_name=tool.name,
                        args=arguments,
                    )

                # Step 6: Upstream Circuit Breaker & Handler Execution
                breaker = self.circuit_breakers[tool.upstream_name]
                raw_result = await breaker.execute(tool.handler, validated_input)

                # Standardize to ToolResult
                if isinstance(raw_result, ToolResult):
                    result = raw_result
                elif isinstance(raw_result, dict):
                    result = ToolResult.json_data(raw_result)
                else:
                    result = ToolResult.text(str(raw_result))

                return result

        except ConfirmationRequiredError as conf_err:
            status = "blocked"
            error_code = conf_err.code
            # Confirmation required is a valid prompt response in Step 1
            return ToolResult.json_data(
                conf_err.to_envelope()["error"],
                is_error=False,
                metadata={"confirmation_required": True},
            )
        except MCPControlPlaneError as mcp_err:
            status = "blocked" if "BLOCKED" in mcp_err.code or "DENIED" in mcp_err.code else "error"
            error_code = mcp_err.code
            raise
        except Exception as unhandled_err:
            status = "error"
            error_code = "UPSTREAM_UNHANDLED"
            logger.error("unhandled_tool_exception", tool=tool.name, error=str(unhandled_err))
            from mcp_control_plane.core.errors import UpstreamError

            raise UpstreamError(
                upstream_name=tool.upstream_name,
                message="An internal upstream error occurred while executing the tool.",
            ) from unhandled_err
        finally:
            duration_sec = time.perf_counter() - start_time
            duration_ms = duration_sec * 1000.0

            # Step 7: Telemetry & Structured Audit Trail
            record_tool_call(
                tool=tool.name,
                server=tool.server,
                duration=duration_sec,
                status=status,
                error_code=error_code or "none",
            )

            audit_entry = AuditRecord(
                request_id=req_id,
                client_id=principal.client_id,
                tool_name=tool.name,
                server=tool.server,
                duration_ms=round(duration_ms, 2),
                status="success"
                if status == "success"
                else ("blocked" if status == "blocked" else "error"),
                error_code=error_code,
                args_summary={
                    k: ("********" if "token" in k or "key" in k else v)
                    for k, v in arguments.items()
                },
                timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            )
            logger.info("mcp_tool_invocation_audit", **audit_entry.model_dump())
