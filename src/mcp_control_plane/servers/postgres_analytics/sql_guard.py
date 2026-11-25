"""sqlglot AST guardrail, validator, and LIMIT rewriter for read-only SQL queries."""

from __future__ import annotations

import functools

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from mcp_control_plane.core.errors import GuardrailBlockedError
from mcp_control_plane.core.metrics import record_guardrail_block

FORBIDDEN_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Into,
    exp.Merge,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Pragma,
)

# Additional forbidden types if present in sqlglot
if hasattr(exp, "TruncateTable"):
    FORBIDDEN_NODE_TYPES += (exp.TruncateTable,)  # type: ignore[assignment]
if hasattr(exp, "Truncate"):
    FORBIDDEN_NODE_TYPES += (exp.Truncate,)  # type: ignore[assignment]
if hasattr(exp, "Lock"):
    FORBIDDEN_NODE_TYPES += (exp.Lock,)  # type: ignore[assignment]

FORBIDDEN_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "query_to_xml",
}

FORBIDDEN_TABLES_AND_SCHEMAS = {
    "pg_catalog",
    "pg_shadow",
    "pg_authid",
    "pg_roles",
    "pg_user",
    "pg_database",
    "information_schema",
}

MAX_ROW_LIMIT = 100


@functools.lru_cache(maxsize=4096)
def _cached_ast_sanitize(clean_sql: str) -> str:
    """Internal LRU-cached AST validation and LIMIT rewrite.

    Valid, clean SQL queries are cached to eliminate repeated AST parsing overhead.
    """
    try:
        statements = sqlglot.parse(clean_sql, read="postgres")
    except ParseError as parse_err:
        raise GuardrailBlockedError(
            f"SQL syntax error: {parse_err}",
            guardrail_type="sql_syntax",
        ) from parse_err
    except Exception as exc:
        raise GuardrailBlockedError(f"Failed to parse SQL: {exc}") from exc

    valid_stmts = [s for s in statements if s is not None]
    if len(valid_stmts) != 1:
        raise GuardrailBlockedError(
            "Stacked queries or multiple statements are strictly prohibited.",
            guardrail_type="stacked_query",
        )

    root = valid_stmts[0]

    # Verify root is a SELECT statement (which accommodates CTEs via root.args.get('with'))
    if not isinstance(root, exp.Select):
        raise GuardrailBlockedError(
            f"Query root must be a SELECT statement, received: '{type(root).__name__}'.",
            guardrail_type="sql_ast",
        )

    # Walk entire AST to catch any embedded mutating nodes (even inside CTEs or subqueries)
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            node_name = type(node).__name__
            raise GuardrailBlockedError(
                f"Prohibited mutating or DDL/DML operation detected: '{node_name}'.",
                guardrail_type="sql_ast",
                details={"node": node_name},
            )

        # Check function invocations
        if isinstance(node, (exp.Anonymous, exp.Func)):
            func_name = node.name.lower() if hasattr(node, "name") else ""
            if func_name in FORBIDDEN_FUNCTIONS:
                raise GuardrailBlockedError(
                    f"Execution of unsafe Postgres function '{func_name}' is forbidden.",
                    guardrail_type="sql_function",
                    details={"function": func_name},
                )

        # Check table and schema accesses
        if isinstance(node, exp.Table):
            table_name = node.name.lower() if node.name else ""
            schema_name = node.db.lower() if node.db else ""
            if (
                table_name in FORBIDDEN_TABLES_AND_SCHEMAS
                or schema_name in FORBIDDEN_TABLES_AND_SCHEMAS
            ):
                target = f"{schema_name}.{table_name}" if schema_name else table_name
                raise GuardrailBlockedError(
                    f"Access to sensitive system catalog or schema '{target}' is forbidden.",
                    guardrail_type="sql_catalog",
                    details={"target": target},
                )

    # Enforce and clamp LIMIT <= 100
    limit_exp = root.args.get("limit")
    if limit_exp is None:
        root = root.limit(MAX_ROW_LIMIT)
    else:
        # Check current limit value
        limit_val_exp = limit_exp.expression
        if isinstance(limit_val_exp, exp.Literal) and limit_val_exp.is_int:
            current_limit = int(limit_val_exp.this)
            if current_limit > MAX_ROW_LIMIT or current_limit < 1:
                root = root.limit(MAX_ROW_LIMIT, copy=False)
        else:
            root = root.limit(MAX_ROW_LIMIT, copy=False)

    return root.sql(dialect="postgres")


def clear_sql_guard_cache() -> None:
    """Clear internal AST validation cache."""
    _cached_ast_sanitize.cache_clear()


def validate_and_sanitize_sql(sql_query: str) -> str:
    """Validate an input SQL query against zero-trust AST guardrails and clamp LIMIT <= 100.

    1. Parse query with dialect postgres.
    2. Block syntax errors & multi-statement/stacked queries.
    3. Verify root node is an exp.Select (allowing safe WITH ... SELECT CTEs).
    4. Walk entire AST to reject any mutating/DDL/DML nodes.
    5. Block dangerous Postgres functions.
    6. Block system catalog schemas and sensitive tables.
    7. Automatically clamp or inject LIMIT <= 100.
    8. Accelerates recurring queries via an internal AST LRU cache.
    """
    clean_sql = sql_query.strip().rstrip(";")
    if not clean_sql:
        record_guardrail_block("empty_sql", tool="run_readonly_query")
        raise GuardrailBlockedError("SQL query is empty.")

    try:
        return _cached_ast_sanitize(clean_sql)
    except GuardrailBlockedError as e:
        record_guardrail_block(e.guardrail_type or "sql_blocked", tool="run_readonly_query")
        raise
