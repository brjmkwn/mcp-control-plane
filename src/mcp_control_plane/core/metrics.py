"""Prometheus telemetry counters, histograms, and gauges."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Counter for total tool calls: labeled by tool, server, status ("success"|"error"), error_code
TOOL_CALLS_TOTAL = Counter(
    "mcp_tool_calls_total",
    "Total number of MCP tool invocations",
    labelnames=["tool", "server", "status", "error_code"],
)

# Histogram for tool duration in seconds: labeled by tool, server
TOOL_DURATION_SECONDS = Histogram(
    "mcp_tool_duration_seconds",
    "Execution duration of MCP tool calls in seconds",
    labelnames=["tool", "server"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# Counter for guardrail and security blocks: labeled by guardrail_type, tool
GUARDRAIL_BLOCKS_TOTAL = Counter(
    "mcp_guardrail_blocks_total",
    "Total number of requests blocked by security policies or AST guardrails",
    labelnames=["guardrail_type", "tool"],
)

# Gauge for upstream circuit breaker state: 0 = CLOSED, 1 = OPEN, 2 = HALF_OPEN
CIRCUIT_BREAKER_STATE = Gauge(
    "mcp_circuit_breaker_state",
    "Current state of upstream circuit breaker (0=closed, 1=open, 2=half_open)",
    labelnames=["upstream"],
)


def record_tool_call(
    tool: str,
    server: str,
    duration: float,
    status: str = "success",
    error_code: str = "none",
) -> None:
    """Record a completed tool invocation in Prometheus metrics."""
    TOOL_CALLS_TOTAL.labels(
        tool=tool,
        server=server,
        status=status,
        error_code=error_code,
    ).inc()
    TOOL_DURATION_SECONDS.labels(
        tool=tool,
        server=server,
    ).observe(duration)


def record_guardrail_block(guardrail_type: str, tool: str = "unknown") -> None:
    """Record an access or guardrail block."""
    GUARDRAIL_BLOCKS_TOTAL.labels(
        guardrail_type=guardrail_type,
        tool=tool,
    ).inc()


def set_circuit_breaker_state(upstream: str, state_value: int) -> None:
    """Update circuit breaker state gauge (0=CLOSED, 1=OPEN, 2=HALF_OPEN)."""
    CIRCUIT_BREAKER_STATE.labels(upstream=upstream).set(state_value)


def get_latest_metrics() -> tuple[bytes, str]:
    """Generate latest metrics in Prometheus text exposition format."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
