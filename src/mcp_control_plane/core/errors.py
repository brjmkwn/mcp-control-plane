"""Structured LLM-recoverable error taxonomy and envelope serialization."""

from __future__ import annotations

from typing import Any


class MCPControlPlaneError(Exception):
    """Base exception for all mcp-control-plane domain errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        suggestions: list[str] | None = None,
        status_code: int = 500,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.details = details or {}
        self.suggestions = suggestions or []
        self.status_code = status_code

    def to_envelope(self) -> dict[str, Any]:
        """Serialize error to standard LLM-recoverable JSON envelope."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
                "suggestions": self.suggestions,
            }
        }


class AuthError(MCPControlPlaneError):
    """Raised when authentication fails (missing or invalid Bearer token)."""

    def __init__(
        self,
        message: str = "Authentication required. Provide a valid Bearer token.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="UNAUTHENTICATED",
            retryable=False,
            details=details or {},
            suggestions=[
                "Include header 'Authorization: Bearer <token>' in your request.",
                "Ensure your API token has not expired or been revoked.",
            ],
            status_code=401,
        )


class OriginDeniedError(MCPControlPlaneError):
    """Raised when Origin header does not match the configured allowlist (DNS rebinding)."""

    def __init__(
        self,
        origin: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = details or {}
        if origin:
            d["rejected_origin"] = origin
        super().__init__(
            message="Request Origin is not authorized.",
            code="ORIGIN_DENIED",
            retryable=False,
            details=d,
            suggestions=[
                "Send requests from an approved domain or local development host.",
                "Verify MCP_ALLOWED_ORIGINS in the gateway configuration.",
            ],
            status_code=403,
        )


class ScopeDeniedError(MCPControlPlaneError):
    """Raised when the authenticated client lacks the scope required for a tool."""

    def __init__(
        self,
        required_scope: str,
        provided_scopes: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = details or {}
        d["required_scope"] = required_scope
        d["provided_scopes"] = provided_scopes or []
        super().__init__(
            message=f"Access denied. Missing required scope: '{required_scope}'.",
            code="FORBIDDEN_SCOPE",
            retryable=False,
            details=d,
            suggestions=[
                f"Obtain a token provisioned with scope '{required_scope}'.",
                "Contact gateway admin to request privilege escalation.",
            ],
            status_code=403,
        )


class ValidationFailedError(MCPControlPlaneError):
    """Raised when tool arguments fail Pydantic schema validation."""

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        suggestions: list[str] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="VALIDATION_FAILED",
            retryable=False,
            details=details or {},
            suggestions=suggestions
            or [
                "Inspect the tool's input schema via 'tools/list'.",
                "Check that required fields are present and conform to declared types.",
            ],
            status_code=400,
        )


class GuardrailBlockedError(MCPControlPlaneError):
    """Raised when an AST guardrail or security policy blocks execution."""

    def __init__(
        self,
        reason: str,
        guardrail_type: str = "ast_guardrail",
        details: dict[str, Any] | None = None,
        suggestions: list[str] | None = None,
    ) -> None:
        d = details or {}
        d["guardrail_type"] = guardrail_type
        d["violation"] = reason
        super().__init__(
            message=f"Guardrail violation: {reason}",
            code="GUARDRAIL_BLOCKED",
            retryable=False,
            details=d,
            suggestions=suggestions
            or [
                "Ensure query is strictly a read-only SELECT or CTE without mutating clauses.",
                "Do not access system schemas or dangerous SQL functions.",
                "Verify input parameters do not contain destructive operations.",
            ],
            status_code=400,
        )


class ConfirmationRequiredError(MCPControlPlaneError):
    """Raised on Step 1 of a mutating tool when a confirmation token is required."""

    def __init__(
        self,
        preview: dict[str, Any],
        confirmation_token: str,
        expires_in_seconds: int = 120,
    ) -> None:
        super().__init__(
            message="Confirmation required for mutating action.",
            code="CONFIRMATION_REQUIRED",
            retryable=True,
            details={
                "preview": preview,
                "confirmation_token": confirmation_token,
                "expires_in_seconds": expires_in_seconds,
            },
            suggestions=[
                "Review the preview payload for side effects.",
                "To proceed, call the tool again with the identical arguments plus 'confirmation_token'.",
            ],
            status_code=200,  # Return 200 with structured instructions as designed
        )


class RateLimitExceededError(MCPControlPlaneError):
    """Raised when a client exceeds per-tool rate limits."""

    def __init__(
        self,
        retry_after_ms: int,
        limit_rpm: int,
        tool_name: str,
    ) -> None:
        super().__init__(
            message=f"Rate limit exceeded for tool '{tool_name}'.",
            code="RATE_LIMITED",
            retryable=True,
            details={
                "tool_name": tool_name,
                "limit_rpm": limit_rpm,
                "retry_after_ms": retry_after_ms,
            },
            suggestions=[
                f"Wait {retry_after_ms}ms before retrying this tool call.",
                "Implement exponential backoff and jitter in client tool dispatch.",
            ],
            status_code=429,
        )


class ConcurrencyLimitError(MCPControlPlaneError):
    """Raised when per-tool concurrency semaphore limit is saturated."""

    def __init__(
        self,
        tool_name: str,
        max_concurrency: int,
        retry_after_ms: int = 100,
    ) -> None:
        super().__init__(
            message=f"Concurrency limit reached for tool '{tool_name}'.",
            code="CONCURRENCY_LIMIT",
            retryable=True,
            details={
                "tool_name": tool_name,
                "max_concurrency": max_concurrency,
                "retry_after_ms": retry_after_ms,
            },
            suggestions=[
                f"Tool '{tool_name}' has reached its concurrency cap ({max_concurrency}).",
                f"Back off and retry in {retry_after_ms}ms.",
            ],
            status_code=429,
        )


class CircuitOpenError(MCPControlPlaneError):
    """Raised when upstream circuit breaker is in OPEN state."""

    def __init__(
        self,
        upstream_name: str,
        cooldown_remaining_seconds: float,
    ) -> None:
        super().__init__(
            message=f"Upstream service '{upstream_name}' circuit breaker is OPEN.",
            code="CIRCUIT_BREAKER_OPEN",
            retryable=True,
            details={
                "upstream": upstream_name,
                "cooldown_remaining_seconds": round(cooldown_remaining_seconds, 2),
            },
            suggestions=[
                f"Upstream '{upstream_name}' is currently failing. Do not hammer the service.",
                f"Retry after cooldown window ({round(cooldown_remaining_seconds, 1)}s).",
            ],
            status_code=503,
        )


class UpstreamError(MCPControlPlaneError):
    """Raised when upstream call fails unexpectedly."""

    def __init__(
        self,
        upstream_name: str,
        message: str,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = details or {}
        d["upstream"] = upstream_name
        super().__init__(
            message=f"Upstream error from '{upstream_name}': {message}",
            code="UPSTREAM_ERROR",
            retryable=retryable,
            details=d,
            suggestions=[
                "Check upstream service status or health dashboard.",
                "Retry with exponential backoff if error is transient.",
            ],
            status_code=502,
        )
