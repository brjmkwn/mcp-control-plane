"""Data models for JSON-RPC 2.0, MCP tools, and audit records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JsonRpcRequest(BaseModel):
    """Standard JSON-RPC 2.0 Request."""

    model_config = ConfigDict(extra="ignore")

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcResponse(BaseModel):
    """Standard JSON-RPC 2.0 Response."""

    model_config = ConfigDict(extra="ignore")

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: Any = None
    error: dict[str, Any] | None = None


class ToolSpec(BaseModel):
    """Definition and execution policy for an enterprise MCP tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    server: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., Any]
    required_scope: str
    mutating: bool = False
    rate_limit_rpm: int = 120
    max_concurrency: int = 20
    upstream_name: str

    def to_mcp_schema(self) -> dict[str, Any]:
        """Convert tool specification to standard MCP tools/list format."""
        schema = self.input_model.model_json_schema()
        # Clean schema title/unneeded root keys for LLM readability
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": schema,
            "required_scope": self.required_scope,
            "mutating": self.mutating,
            "server": self.server,
        }


class ToolResult(BaseModel):
    """Structured result returned by an MCP tool."""

    model_config = ConfigDict(extra="ignore")

    content: list[dict[str, Any]]
    is_error: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def text(
        cls, text: str, is_error: bool = False, metadata: dict[str, Any] | None = None
    ) -> ToolResult:
        """Helper to create a standard MCP text content result."""
        return cls(
            content=[{"type": "text", "text": text}],
            is_error=is_error,
            metadata=metadata or {},
        )

    @classmethod
    def json_data(
        cls, data: Any, is_error: bool = False, metadata: dict[str, Any] | None = None
    ) -> ToolResult:
        """Helper to create a structured JSON text result."""
        import json

        text = json.dumps(data, indent=2, default=str)
        return cls(
            content=[{"type": "text", "text": text}],
            is_error=is_error,
            metadata=metadata or {},
        )


class AuditRecord(BaseModel):
    """Structured audit trail record emitted for each tool invocation."""

    request_id: str
    client_id: str
    tool_name: str
    server: str
    duration_ms: float
    status: Literal["success", "error", "blocked"]
    error_code: str | None = None
    args_summary: dict[str, Any] = Field(default_factory=dict)
    timestamp: str
