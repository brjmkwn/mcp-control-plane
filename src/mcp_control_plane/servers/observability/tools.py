"""MCP Tools for Service Observability and Telemetry (Server 3)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_control_plane.core.models import ToolResult, ToolSpec
from mcp_control_plane.gateway.registry import ToolRegistry
from mcp_control_plane.servers.observability.telemetry import TelemetryProvider

SERVER_NAME = "observability"
UPSTREAM_NAME = "telemetry"


class QueryLatencyAnomaliesInput(BaseModel):
    """Input for detecting high P95 latency across microservices."""

    threshold_ms: float = Field(
        default=200.0,
        ge=1.0,
        description="P95 latency threshold in milliseconds above which a service is flagged.",
    )
    lookback_minutes: int = Field(
        default=15,
        ge=1,
        le=120,
        description="Time window in minutes to evaluate latency metrics.",
    )


class GetErrorBreakdownInput(BaseModel):
    """Input for aggregating HTTP 4xx/5xx and tool errors by service."""

    service: str | None = Field(
        default=None,
        description="Optional service name filter (e.g. 'order-fulfillment').",
    )
    lookback_minutes: int = Field(
        default=15, ge=1, le=120, description="Lookback window in minutes."
    )


class SearchStructuredLogsInput(BaseModel):
    """Input for querying structured JSON application and gateway logs."""

    service: str | None = Field(default=None, description="Filter by service name.")
    severity: str | None = Field(
        default=None, description="Filter by severity level ('INFO', 'WARN', 'ERROR')."
    )
    correlation_id: str | None = Field(default=None, description="Trace or correlation ID.")
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum number of log records to return."
    )


class DiagnoseServiceHealthInput(BaseModel):
    """Input for running comprehensive diagnostic health checks on a service."""

    service_name: str = Field(description="Target service name to diagnose.")


def register_observability_tools(registry: ToolRegistry, provider: TelemetryProvider) -> None:
    """Register all 4 observability MCP tools into the gateway registry."""

    async def query_latency_anomalies_handler(params: QueryLatencyAnomaliesInput) -> ToolResult:
        anomalies = await provider.query_latency_anomalies(
            threshold_ms=params.threshold_ms,
            lookback_minutes=params.lookback_minutes,
        )
        return ToolResult.json_data({"anomalies": anomalies, "count": len(anomalies)})

    async def get_error_breakdown_handler(params: GetErrorBreakdownInput) -> ToolResult:
        breakdown = await provider.get_error_breakdown(
            service=params.service,
            lookback_minutes=params.lookback_minutes,
        )
        return ToolResult.json_data({"errors": breakdown, "count": len(breakdown)})

    async def search_structured_logs_handler(params: SearchStructuredLogsInput) -> ToolResult:
        logs = await provider.search_structured_logs(
            service=params.service,
            severity=params.severity,
            correlation_id=params.correlation_id,
            limit=params.limit,
        )
        return ToolResult.json_data({"logs": logs, "count": len(logs)})

    async def diagnose_service_health_handler(params: DiagnoseServiceHealthInput) -> ToolResult:
        health = await provider.diagnose_service_health(params.service_name)
        return ToolResult.json_data(health)

    # 1. query_latency_anomalies
    registry.register(
        ToolSpec(
            name="query_latency_anomalies",
            server=SERVER_NAME,
            description="Detect microservices whose P95 latency exceeds a threshold over a time window.",
            input_model=QueryLatencyAnomaliesInput,
            handler=query_latency_anomalies_handler,
            required_scope="read:ops",
            mutating=False,
            rate_limit_rpm=120,
            max_concurrency=25,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 2. get_error_breakdown
    registry.register(
        ToolSpec(
            name="get_error_breakdown",
            server=SERVER_NAME,
            description="Aggregate 4xx/5xx and tool error rates grouped by service and error code.",
            input_model=GetErrorBreakdownInput,
            handler=get_error_breakdown_handler,
            required_scope="read:ops",
            mutating=False,
            rate_limit_rpm=120,
            max_concurrency=25,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 3. search_structured_logs
    registry.register(
        ToolSpec(
            name="search_structured_logs",
            server=SERVER_NAME,
            description="Query and filter structured JSON service logs by severity, correlation ID, and service name.",
            input_model=SearchStructuredLogsInput,
            handler=search_structured_logs_handler,
            required_scope="read:ops",
            mutating=False,
            rate_limit_rpm=100,
            max_concurrency=20,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 4. diagnose_service_health
    registry.register(
        ToolSpec(
            name="diagnose_service_health",
            server=SERVER_NAME,
            description="Combine latency, error spikes, and recent logs to produce a typed service health diagnosis.",
            input_model=DiagnoseServiceHealthInput,
            handler=diagnose_service_health_handler,
            required_scope="read:ops",
            mutating=False,
            rate_limit_rpm=60,
            max_concurrency=15,
            upstream_name=UPSTREAM_NAME,
        )
    )
