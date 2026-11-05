"""Database store supporting live PostgreSQL+pgvector and zero-dependency SQLite fallback."""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Protocol

from mcp_control_plane.core.errors import UpstreamError
from mcp_control_plane.core.logging import get_logger

logger = get_logger("postgres_analytics.store")


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    dot = sum(a * b for a, b in zip(v1, v2, strict=False))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class AnalyticsStore(Protocol):
    """Protocol for database analytics operations."""

    async def list_tables(self) -> list[dict[str, Any]]: ...

    async def describe_schema(self, table_name: str) -> list[dict[str, Any]]: ...

    async def run_readonly_query(self, sanitized_sql: str) -> list[dict[str, Any]]: ...

    async def hybrid_search_docs(
        self,
        query_text: str,
        query_vector: list[float] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]: ...


class SQLiteFallbackAnalyticsStore:
    """In-memory SQLite store seeded with deterministic data for local CI/testing."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema_and_seed()

    def _init_schema_and_seed(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                tier TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'healthy',
                owner_team TEXT NOT NULL
            );

            CREATE TABLE deployments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER REFERENCES services(id),
                version TEXT NOT NULL,
                environment TEXT NOT NULL,
                status TEXT NOT NULL,
                deployed_by TEXT NOT NULL
            );

            CREATE TABLE incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER REFERENCES services(id),
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                resolved INTEGER DEFAULT 0
            );

            CREATE TABLE runbook_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT NOT NULL
            );

            INSERT INTO services (name, tier, status, owner_team) VALUES
                ('auth-service', 'tier-0', 'healthy', 'security-team'),
                ('payment-gateway', 'tier-0', 'healthy', 'payments-team'),
                ('order-fulfillment', 'tier-1', 'degraded', 'logistics-team'),
                ('notification-worker', 'tier-2', 'healthy', 'platform-team');

            INSERT INTO deployments (service_id, version, environment, status, deployed_by) VALUES
                (1, 'v2.4.1', 'production', 'success', 'ci-bot'),
                (2, 'v1.8.0', 'production', 'success', 'brij-admin'),
                (3, 'v3.1.2', 'staging', 'failed', 'ci-bot'),
                (4, 'v1.0.5', 'production', 'success', 'ci-bot');

            INSERT INTO incidents (service_id, severity, title, resolved) VALUES
                (3, 'P1', 'High order fulfillment processing latency spike', 0),
                (2, 'P2', 'Transient webhook callback drops from upstream processor', 1);

            INSERT INTO runbook_docs (title, category, content, embedding_json) VALUES
                ('Payment Gateway Timeout Remediation', 'runbook',
                 'Steps to recover when payment gateway latency exceeds 5000ms: check upstream provider status, toggle fallback gateway.',
                 '[0.91, 0.12, 0.35, 0.05]'),
                ('Database Connection Pool Starvation', 'runbook',
                 'Diagnosing connection pool exhaustion: inspect pg_stat_activity, scale pgbouncer max_client_conn, restart idle workers.',
                 '[0.15, 0.88, 0.22, 0.41]'),
                ('Authentication Token Validation Failure', 'runbook',
                 'Remediation guide for sudden spike in 401s: verify public JWKS cache expiration and clock skew tolerance.',
                 '[0.85, 0.08, 0.45, 0.11]'),
                ('Worker Queue Backlog Drain Protocol', 'runbook',
                 'How to drain Celery/Redis queue backlog during high throughput bursts: scale replicas and increase concurrency.',
                 '[0.20, 0.30, 0.85, 0.35]');
            """
        )
        self.conn.commit()

    async def list_tables(self) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        )
        tables = [row["name"] for row in cur.fetchall()]
        results = []
        for tbl in tables:
            cur.execute(f"SELECT COUNT(*) AS count FROM {tbl}")
            count = cur.fetchone()["count"]
            results.append({"table_name": tbl, "row_count_estimate": count})
        return results

    async def describe_schema(self, table_name: str) -> list[dict[str, Any]]:
        # Enforce alphanumeric table name
        if not table_name.replace("_", "").isalnum():
            raise UpstreamError("database", f"Invalid table name format: '{table_name}'.")

        cur = self.conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name});")
        rows = cur.fetchall()
        if not rows:
            raise UpstreamError("database", f"Table '{table_name}' does not exist.")

        return [
            {
                "column_name": r["name"],
                "data_type": r["type"],
                "is_nullable": "YES" if not r["notnull"] else "NO",
                "is_primary_key": bool(r["pk"]),
            }
            for r in rows
        ]

    async def run_readonly_query(self, sanitized_sql: str) -> list[dict[str, Any]]:
        try:
            cur = self.conn.cursor()
            cur.execute(sanitized_sql)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            raise UpstreamError("database", f"Query execution failed: {e}") from e

    async def hybrid_search_docs(
        self,
        query_text: str,
        query_vector: list[float] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        import json

        cur = self.conn.cursor()
        cur.execute("SELECT id, title, category, content, embedding_json FROM runbook_docs")
        docs = cur.fetchall()

        q_lower = query_text.lower()
        scored_docs = []

        # Default vector if none provided: deterministic normalized token count
        vec = query_vector or [0.7, 0.2, 0.3, 0.1]

        for d in docs:
            # Lexical keyword score
            lexical_hits = 0
            for term in q_lower.split():
                if term in d["title"].lower():
                    lexical_hits += 2.0
                if term in d["content"].lower():
                    lexical_hits += 1.0

            # Vector cosine similarity
            embedding = json.loads(d["embedding_json"])
            cos_sim = cosine_similarity(vec, embedding)

            # Combined hybrid score (0.4 * lexical + 0.6 * dense vector)
            combined_score = (0.4 * min(1.0, lexical_hits / 3.0)) + (0.6 * max(0.0, cos_sim))

            scored_docs.append(
                {
                    "id": d["id"],
                    "title": d["title"],
                    "category": d["category"],
                    "snippet": d["content"][:160] + "...",
                    "relevance_score": round(combined_score, 4),
                }
            )

        scored_docs.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored_docs[:limit]


class PostgresAnalyticsStore:
    """Live PostgreSQL + pgvector store using asyncpg with connection timeout and read-only transactions."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def _get_conn(self) -> Any:
        import asyncpg

        return await asyncpg.connect(self.dsn, timeout=5.0)

    async def list_tables(self) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        try:
            rows = await conn.fetch(
                """
                SELECT table_name,
                       COALESCE(reltuples::bigint, 0) as row_count_estimate
                FROM information_schema.tables t
                LEFT JOIN pg_class c ON c.relname = t.table_name
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE';
                """
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def describe_schema(self, table_name: str) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        try:
            rows = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                ORDER BY ordinal_position;
                """,
                table_name,
            )
            if not rows:
                raise UpstreamError("database", f"Table '{table_name}' does not exist.")
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def run_readonly_query(self, sanitized_sql: str) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        try:
            # Enforce read-only transaction and statement timeout
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = '3000ms';")
                rows = await conn.fetch(sanitized_sql)
                return [dict(r) for r in rows]
        except Exception as e:
            raise UpstreamError("database", f"PostgreSQL execution error: {e}") from e
        finally:
            await conn.close()

    async def hybrid_search_docs(
        self,
        query_text: str,
        query_vector: list[float] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        try:
            vec = query_vector or [0.5, 0.5, 0.5, 0.5]
            vec_str = f"[{','.join(str(x) for x in vec)}]"
            rows = await conn.fetch(
                """
                SELECT id, title, category, SUBSTRING(content FROM 1 FOR 160) as snippet,
                       (1.0 - (embedding <=> $1::vector)) as relevance_score
                FROM runbook_docs
                ORDER BY embedding <=> $1::vector ASC
                LIMIT $2;
                """,
                vec_str,
                limit,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()
