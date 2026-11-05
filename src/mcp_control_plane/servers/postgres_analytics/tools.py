"""MCP Tools for PostgreSQL & Vector Analytics (Server 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_control_plane.core.models import ToolResult, ToolSpec
from mcp_control_plane.gateway.registry import ToolRegistry
from mcp_control_plane.servers.postgres_analytics.sql_guard import validate_and_sanitize_sql
from mcp_control_plane.servers.postgres_analytics.store import AnalyticsStore

SERVER_NAME = "postgres_analytics"
UPSTREAM_NAME = "database"


class ListTablesInput(BaseModel):
    """Input for listing available database tables."""

    include_views: bool = Field(default=False, description="Whether to include database views.")


class DescribeSchemaInput(BaseModel):
    """Input for describing columns and types of a table."""

    table_name: str = Field(description="Name of the public table to inspect.")


class RunReadonlyQueryInput(BaseModel):
    """Input for running an AST-validated read-only SQL query."""

    query: str = Field(description="Read-only SELECT or CTE query to execute.")


class HybridSearchDocsInput(BaseModel):
    """Input for hybrid lexical + dense vector search across operational runbooks."""

    query: str = Field(description="Natural language keyword search string.")
    query_vector: list[float] | None = Field(
        default=None,
        description="Optional 4-dimensional embedding vector for semantic similarity.",
    )
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of runbooks to return.")


def register_postgres_analytics_tools(registry: ToolRegistry, store: AnalyticsStore) -> None:
    """Register all 4 postgres analytics MCP tools into the gateway registry."""

    async def list_tables_handler(params: ListTablesInput) -> ToolResult:
        tables = await store.list_tables()
        return ToolResult.json_data({"tables": tables, "total": len(tables)})

    async def describe_schema_handler(params: DescribeSchemaInput) -> ToolResult:
        schema = await store.describe_schema(params.table_name)
        return ToolResult.json_data({"table": params.table_name, "columns": schema})

    async def run_readonly_query_handler(params: RunReadonlyQueryInput) -> ToolResult:
        # Step 1: AST validation & limit clamping via sqlglot
        sanitized_sql = validate_and_sanitize_sql(params.query)
        # Step 2: Read-only execution in store
        rows = await store.run_readonly_query(sanitized_sql)
        return ToolResult.json_data(
            {"rows": rows, "count": len(rows), "executed_sql": sanitized_sql},
            metadata={"sanitized_sql": sanitized_sql},
        )

    async def hybrid_search_docs_handler(params: HybridSearchDocsInput) -> ToolResult:
        results = await store.hybrid_search_docs(
            query_text=params.query,
            query_vector=params.query_vector,
            limit=params.limit,
        )
        return ToolResult.json_data({"query": params.query, "results": results})

    # Register list_tables
    registry.register(
        ToolSpec(
            name="list_tables",
            server=SERVER_NAME,
            description="List public database tables and estimated row counts.",
            input_model=ListTablesInput,
            handler=list_tables_handler,
            required_scope="read:db",
            mutating=False,
            rate_limit_rpm=120,
            max_concurrency=25,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # Register describe_schema
    registry.register(
        ToolSpec(
            name="describe_schema",
            server=SERVER_NAME,
            description="Inspect column names, data types, nullability, and keys for a specific table.",
            input_model=DescribeSchemaInput,
            handler=describe_schema_handler,
            required_scope="read:db",
            mutating=False,
            rate_limit_rpm=120,
            max_concurrency=25,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # Register run_readonly_query
    registry.register(
        ToolSpec(
            name="run_readonly_query",
            server=SERVER_NAME,
            description="Execute an AST-guarded, read-only SQL SELECT query with auto-clamped LIMIT <= 100.",
            input_model=RunReadonlyQueryInput,
            handler=run_readonly_query_handler,
            required_scope="read:db",
            mutating=False,
            rate_limit_rpm=60,
            max_concurrency=15,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # Register hybrid_search_docs
    registry.register(
        ToolSpec(
            name="hybrid_search_docs",
            server=SERVER_NAME,
            description="Perform hybrid keyword + dense vector semantic search over operational runbooks.",
            input_model=HybridSearchDocsInput,
            handler=hybrid_search_docs_handler,
            required_scope="read:db",
            mutating=False,
            rate_limit_rpm=100,
            max_concurrency=20,
            upstream_name=UPSTREAM_NAME,
        )
    )
