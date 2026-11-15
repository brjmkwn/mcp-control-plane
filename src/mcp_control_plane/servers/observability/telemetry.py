"""Telemetry and log analytics provider for live Prometheus and deterministic local fixture store."""

from __future__ import annotations

import datetime
from typing import Any, Protocol

import httpx

from mcp_control_plane.core.logging import get_logger

logger = get_logger("observability.telemetry")


class TelemetryProvider(Protocol):
    """Protocol for system health and observability diagnostics."""

    async def query_latency_anomalies(
        self,
        threshold_ms: float = 200.0,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]: ...

    async def get_error_breakdown(
        self,
        service: str | None = None,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]: ...

    async def search_structured_logs(
        self,
        service: str | None = None,
        severity: str | None = None,
        correlation_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    async def diagnose_service_health(
        self,
        service_name: str,
    ) -> dict[str, Any]: ...


class DeterministicTelemetryStore:
    """Deterministic telemetry fixture store for local tests, CI, and reproducible benchmarks."""

    def __init__(self) -> None:
        self.services_metrics = {
            "order-fulfillment": {"p95_ms": 485.0, "p50_ms": 120.0, "rpm": 450, "error_pct": 8.4},
            "payment-gateway": {"p95_ms": 255.0, "p50_ms": 65.0, "rpm": 320, "error_pct": 2.1},
            "auth-service": {"p95_ms": 42.0, "p50_ms": 12.0, "rpm": 1200, "error_pct": 0.2},
            "notification-worker": {"p95_ms": 15.0, "p50_ms": 4.0, "rpm": 150, "error_pct": 0.0},
        }

        self.error_counts = [
            {"service": "order-fulfillment", "error_code": "504_GATEWAY_TIMEOUT", "count": 142},
            {"service": "order-fulfillment", "error_code": "500_INTERNAL_ERROR", "count": 18},
            {"service": "payment-gateway", "error_code": "502_BAD_GATEWAY", "count": 34},
            {"service": "auth-service", "error_code": "401_UNAUTHORIZED", "count": 8},
        ]

        self.structured_logs = [
            {
                "timestamp": "2026-11-24T20:15:32Z",
                "service": "order-fulfillment",
                "severity": "ERROR",
                "message": "Connection to upstream payment-gateway timed out after 5000ms",
                "correlation_id": "corr-ord-9021",
                "latency_ms": 5002.4,
            },
            {
                "timestamp": "2026-11-24T20:15:40Z",
                "service": "order-fulfillment",
                "severity": "ERROR",
                "message": "Checkout transaction rolled back due to gateway failure",
                "correlation_id": "corr-ord-9021",
                "latency_ms": 12.0,
            },
            {
                "timestamp": "2026-11-24T20:16:01Z",
                "service": "payment-gateway",
                "severity": "WARN",
                "message": "Third-party payment processor latency exceeds baseline P99 (2400ms)",
                "correlation_id": "corr-pay-4412",
                "latency_ms": 2415.8,
            },
            {
                "timestamp": "2026-11-24T20:16:15Z",
                "service": "auth-service",
                "severity": "INFO",
                "message": "JWKS key rotation completed successfully",
                "correlation_id": "corr-sec-1120",
                "latency_ms": 3.2,
            },
        ]

    async def query_latency_anomalies(
        self,
        threshold_ms: float = 200.0,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        anomalies = []
        for name, metrics in self.services_metrics.items():
            if metrics["p95_ms"] > threshold_ms:
                anomalies.append(
                    {
                        "service": name,
                        "p95_latency_ms": metrics["p95_ms"],
                        "threshold_ms": threshold_ms,
                        "lookback_minutes": lookback_minutes,
                        "deviation_ratio": round(metrics["p95_ms"] / threshold_ms, 2),
                    }
                )
        anomalies.sort(key=lambda x: x["p95_latency_ms"], reverse=True)
        return anomalies

    async def get_error_breakdown(
        self,
        service: str | None = None,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        entries = self.error_counts
        if service:
            entries = [e for e in entries if e["service"].lower() == service.lower()]
        return [
            {
                **e,
                "lookback_minutes": lookback_minutes,
            }
            for e in entries
        ]

    async def search_structured_logs(
        self,
        service: str | None = None,
        severity: str | None = None,
        correlation_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        matches = self.structured_logs
        if service:
            matches = [m for m in matches if m["service"].lower() == service.lower()]
        if severity:
            matches = [m for m in matches if m["severity"].upper() == severity.upper()]
        if correlation_id:
            matches = [m for m in matches if m["correlation_id"] == correlation_id]
        return matches[:limit]

    async def diagnose_service_health(self, service_name: str) -> dict[str, Any]:
        metrics = self.services_metrics.get(service_name)
        if not metrics:
            return {
                "service": service_name,
                "status": "UNKNOWN",
                "message": f"Service '{service_name}' not found in active telemetry catalog.",
                "recommendations": ["Verify service name spelling against 'services' catalog."],
            }

        p95 = metrics["p95_ms"]
        error_pct = metrics["error_pct"]

        if error_pct > 5.0 or p95 > 400.0:
            status = "CRITICAL"
            actions = [
                "Execute incident triage issue for upstream investigation.",
                "Inspect downstream payment gateway latency and connection pools.",
                "Scale service replicas if CPU or worker threads are saturated.",
            ]
        elif error_pct > 1.0 or p95 > 200.0:
            status = "DEGRADED"
            actions = [
                "Monitor upstream dependency latencies over 15-minute window.",
                "Check recent deployment diffs for configuration regressions.",
            ]
        else:
            status = "HEALTHY"
            actions = ["No immediate action needed. Metrics are within baseline SLA."]

        service_errors = [e for e in self.error_counts if e["service"] == service_name]
        return {
            "service": service_name,
            "status": status,
            "p95_latency_ms": p95,
            "p50_latency_ms": metrics["p50_ms"],
            "error_rate_pct": error_pct,
            "error_breakdown": service_errors,
            "diagnosed_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "recommended_actions": actions,
        }


class LivePrometheusTelemetryProvider:
    """Production telemetry provider querying Prometheus HTTP API with fallback to deterministic store."""

    def __init__(self, prometheus_url: str) -> None:
        self.url = prometheus_url.rstrip("/")
        self.fallback = DeterministicTelemetryStore()

    async def _query_promql(self, query: str) -> list[dict[str, Any]]:
        endpoint = f"{self.url}/api/v1/query"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(endpoint, params={"query": query})
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("result", [])

    async def query_latency_anomalies(
        self,
        threshold_ms: float = 200.0,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        try:
            # PromQL: P95 latency by tool
            promql = f"histogram_quantile(0.95, sum(rate(mcp_tool_duration_seconds_bucket[{lookback_minutes}m])) by (le, tool)) * 1000"
            results = await self._query_promql(promql)
            anomalies = []
            for item in results:
                tool = item.get("metric", {}).get("tool", "unknown")
                val = float(item.get("value", [0, 0])[1])
                if val > threshold_ms:
                    anomalies.append(
                        {
                            "service": tool,
                            "p95_latency_ms": round(val, 2),
                            "threshold_ms": threshold_ms,
                            "lookback_minutes": lookback_minutes,
                            "deviation_ratio": round(val / threshold_ms, 2),
                        }
                    )
            return anomalies
        except Exception as e:
            logger.warning("prometheus_query_failed_using_fallback", error=str(e))
            return await self.fallback.query_latency_anomalies(threshold_ms, lookback_minutes)

    async def get_error_breakdown(
        self,
        service: str | None = None,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        try:
            promql = f'sum(rate(mcp_tool_calls_total{{status="error"}}[{lookback_minutes}m])) by (tool, error_code)'
            results = await self._query_promql(promql)
            breakdown = []
            for item in results:
                metric = item.get("metric", {})
                count = float(item.get("value", [0, 0])[1])
                breakdown.append(
                    {
                        "service": metric.get("tool", "unknown"),
                        "error_code": metric.get("error_code", "unknown"),
                        "count": int(count),
                        "lookback_minutes": lookback_minutes,
                    }
                )
            if service:
                breakdown = [b for b in breakdown if b["service"].lower() == service.lower()]
            return breakdown
        except Exception:
            return await self.fallback.get_error_breakdown(service, lookback_minutes)

    async def search_structured_logs(
        self,
        service: str | None = None,
        severity: str | None = None,
        correlation_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # Log store fallback (or Loki/OpenSearch integration)
        return await self.fallback.search_structured_logs(service, severity, correlation_id, limit)

    async def diagnose_service_health(self, service_name: str) -> dict[str, Any]:
        return await self.fallback.diagnose_service_health(service_name)
