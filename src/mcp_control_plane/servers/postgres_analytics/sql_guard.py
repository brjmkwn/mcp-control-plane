"""sqlglot AST guardrail, validator, and LIMIT rewriter for read-only SQL queries."""

from __future__ import annotations

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


def validate_and_sanitize_sql(sql_query: str) -> str:
    """Validate an input SQL query against zero-trust AST guardrails and clamp LIMIT <= 100.

    1. Parse query with dialect postgres.
    2. Block syntax errors & multi-statement/stacked queries.
    3. Verify root node is an exp.Select (allowing safe WITH ... SELECT CTEs).
    4. Walk entire AST to reject any mutating/DDL/DML nodes.
    5. Block dangerous Postgres functions.
    6. Block system catalog schemas and sensitive tables.
    7. Automatically clamp or inject LIMIT <= 100.
    """
    clean_sql = sql_query.strip().rstrip(";")
    if not clean_sql:
        record_guardrail_block("empty_sql", tool="run_readonly_query")
        raise GuardrailBlockedError("SQL query is empty.")

    try:
        statements = sqlglot.parse(clean_sql, read="postgres")
    except ParseError as parse_err:
        record_guardrail_block("sql_syntax_error", tool="run_readonly_query")
        raise GuardrailBlockedError(
            f"SQL syntax error: {parse_err}",
            guardrail_type="sql_syntax",
        ) from parse_err
    except Exception as exc:
        record_guardrail_block("sql_parse_failed", tool="run_readonly_query")
        raise GuardrailBlockedError(f"Failed to parse SQL: {exc}") from exc

    valid_stmts = [s for s in statements if s is not None]
    if len(valid_stmts) != 1:
        record_guardrail_block("sql_stacked_query", tool="run_readonly_query")
        raise GuardrailBlockedError(
            "Stacked queries or multiple statements are strictly prohibited."
        )

    root = valid_stmts[0]

    # Verify root is a SELECT statement (which accommodates CTEs via root.args.get('with'))
    if not isinstance(root, exp.Select):
        record_guardrail_block("sql_non_select_root", tool="run_readonly_query")
        raise GuardrailBlockedError(
            f"Query root must be a SELECT statement, received: '{type(root).__name__}'.",
            guardrail_type="sql_ast",
        )

    # Walk entire AST to catch any embedded mutating nodes (even inside CTEs or subqueries)
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            record_guardrail_block(
                f"sql_forbidden_{type(node).__name__.lower()}", tool="run_readonly_query"
            )
            raise GuardrailBlockedError(
                f"Prohibited mutating or DDL/DML operation detected: '{type(node).__name__}'.",
                guardrail_type="sql_ast",
            )

        # Check function invocations
        if isinstance(node, (exp.Anonymous, exp.Func)):
            func_name = node.name.lower() if hasattr(node, "name") else ""
            if func_name in FORBIDDEN_FUNCTIONS:
                record_guardrail_block("sql_forbidden_function", tool="run_readonly_query")
                raise GuardrailBlockedError(
                    f"Execution of unsafe Postgres function '{func_name}' is forbidden.",
                    guardrail_type="sql_function",
                )

        # Check table and schema accesses
        if isinstance(node, exp.Table):
            table_name = node.name.lower() if node.name else ""
            schema_name = node.db.lower() if node.db else ""
            if (
                table_name in FORBIDDEN_TABLES_AND_SCHEMAS
                or schema_name in FORBIDDEN_TABLES_AND_SCHEMAS
            ):
                record_guardrail_block("sql_system_catalog", tool="run_readonly_query")
                target = f"{schema_name}.{table_name}" if schema_name else table_name
                raise GuardrailBlockedError(
                    f"Access to sensitive system catalog or schema '{target}' is forbidden.",
                    guardrail_type="sql_catalog",
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
