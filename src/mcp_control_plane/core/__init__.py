"""Core utilities: errors, logging, metrics, models."""

from mcp_control_plane.core.errors import (
    AuthError,
    CircuitOpenError,
    ConcurrencyLimitError,
    ConfirmationRequiredError,
    GuardrailBlockedError,
    MCPControlPlaneError,
    OriginDeniedError,
    RateLimitExceededError,
    ScopeDeniedError,
    UpstreamError,
    ValidationFailedError,
)
from mcp_control_plane.core.logging import get_logger, setup_logging
from mcp_control_plane.core.metrics import (
    get_latest_metrics,
    record_guardrail_block,
    record_tool_call,
    set_circuit_breaker_state,
)
from mcp_control_plane.core.models import (
    AuditRecord,
    JsonRpcRequest,
    JsonRpcResponse,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "MCPControlPlaneError",
    "AuthError",
    "OriginDeniedError",
    "ScopeDeniedError",
    "ValidationFailedError",
    "GuardrailBlockedError",
    "ConfirmationRequiredError",
    "RateLimitExceededError",
    "ConcurrencyLimitError",
    "CircuitOpenError",
    "UpstreamError",
    "get_logger",
    "setup_logging",
    "record_tool_call",
    "record_guardrail_block",
    "set_circuit_breaker_state",
    "get_latest_metrics",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "ToolSpec",
    "ToolResult",
    "AuditRecord",
]
