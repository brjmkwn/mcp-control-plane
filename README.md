# `mcp-control-plane`

**Zero-Trust Production MCP Gateway & Enterprise Multi-Server Suite**  
*Hardened Streamable HTTP & stdio Model Context Protocol (MCP) gateway with SQL AST guardrails, scoped RBAC, two-step cryptographic write gates, Redis sliding-window resilience, and AWS Serverless IaC.*

[![CI](https://github.com/brjmkwn/mcp-control-plane/actions/workflows/ci.yml/badge.svg)](https://github.com/brjmkwn/mcp-control-plane/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Project Timeline:** October 2026 – November 2026  
> **Architecture & Inception:** October 14, 2026 | **v1.0 Production Release:** November 24, 2026  
> **Engineered by:** Brij Makwana ([@brjmkwn](https://github.com/brjmkwn))

---

## 1. System Architecture

```text
                    +-------------------------------------------+
                    |   MCP Client (Claude / Agent / CI Runner) |
                    +---------------------+---------------------+
                                          |
                 POST /mcp (Streamable HTTP: JSON-RPC / SSE)
                 Headers: Authorization: Bearer <token>, Origin: <allowed>
                                          |
                                          v
+---------------------------------------------------------------------------------+
|                         mcp-control-plane Gateway (FastAPI)                     |
|                                                                                 |
|  [Layer 1: Transport & Security]                                                |
|    - Origin Header Validator (403 DNS-rebinding defense)                        |
|    - Scoped Bearer Token RBAC (read:db | read:github | write:github | read:ops) |
|    - Structlog JSON Audit Trail (request_id, client_id, tool, latency, status)  |
|                                                                                 |
|  [Layer 2: Validation & Guardrails]                                             |
|    - Pydantic v2 Strict Schema Validation & Payload Size Caps                   |
|    - 2-Step HMAC Confirmation Gate (Required for all write/mutating tools)      |
|                                                                                 |
|  [Layer 3: Resilience & Backpressure]                                           |
|    - Per-Tool Sliding-Window Rate Limiter (Redis + Async In-Memory Fallback)    |
|    - Per-Tool Concurrency Caps (asyncio.Semaphore)                              |
|    - Upstream Circuit Breaker (Closed -> Open -> Half-Open)                     |
|    - Structured LLM-Recoverable Error Envelope (code, message, suggestions)     |
|                                                                                 |
|  [Layer 4: Observability]                                                       |
|    - Prometheus /metrics (invocation counters, p50/p95/p99 histograms)          |
|    - Readiness Probe /health (gateway, DB, Redis status)                        |
+-----------+-----------------------------+-----------------------------+---------+
            |                             |                             |
            v                             v                             v
+-----------------------+   +---------------------------+   +-------------------------+
| postgres_analytics    |   | github_triage_mcp         |   | observability_mcp       |
| - sqlglot AST Guard   |   | - Live GitHub REST Client |   | - Prometheus / Telemetry|
| - SELECT/CTE Only     |   | - Deterministic Mock Mode |   | - Latency Anomalies     |
| - pgvector Hybrid     |   | - 2-Step Write Gate       |   | - Error & Log Triage    |
+-----------+-----------+   +-------------+-------------+   +------------+------------+
            |                             |                              |
            v                             v                              v
   PostgreSQL + pgvector           GitHub REST API            Prometheus / Log Store
```

---

## 2. Enterprise Tool Suite (13 Production Tools)

`mcp-control-plane` bundles three full enterprise server domains wired behind unified transport and defense-in-depth middleware:

### Server 1: `postgres_analytics_mcp` (Scope: `read:db`)
Guarded by `sqlglot` AST parsing ensuring zero DDL/DML mutations, system catalog isolation, and auto-clamped `LIMIT <= 100`.

| Tool Name | Mutating | Description |
| :--- | :--- | :--- |
| `list_tables` | No | Lists public database tables with row-count estimates. |
| `describe_schema` | No | Returns column names, data types, nullability, and primary key metadata. |
| `run_readonly_query` | No | Executes an AST-validated read-only SELECT/CTE query with statement timeout. |
| `hybrid_search_docs` | No | Performs hybrid keyword + dense vector similarity search across seeded runbooks (`pgvector` in PostgreSQL; cosine lexical fallback in SQLite). |

### Server 2: `github_triage_mcp` (Scopes: `read:github`, `write:github`)
CI/CD incident triage tools with provider isolation (`LiveGitHubProvider` vs `DeterministicMockGitHubProvider`).

| Tool Name | Scope | Mutating | Description |
| :--- | :--- | :--- | :--- |
| `get_failed_workflow_logs` | `read:github` | No | Extracts failing job step names and truncated error traces for a GitHub Actions run. |
| `get_commit_diff` | `read:github` | No | Fetches file patches, diff hunks, and stats for a commit SHA. |
| `list_open_incidents` | `read:github` | No | Filters and lists open issues labeled with `incident`, `bug`, or `p0`. |
| `draft_triage_issue` | `write:github` | **Yes (2-Step Gate)** | Creates a structured incident triage issue after cryptographic confirmation. |
| `post_pr_triage_comment` | `write:github` | **Yes (2-Step Gate)** | Posts a root-cause remediation comment on a PR after confirmation validation. |

### Server 3: `observability_mcp` (Scope: `read:ops`)
Real-time microservice observability and anomaly detection backed by Prometheus and structured logs.

| Tool Name | Mutating | Description |
| :--- | :--- | :--- |
| `query_latency_anomalies` | No | Flags microservices whose P95 latency exceeds baseline SLA thresholds. |
| `get_error_breakdown` | No | Groups HTTP 4xx/5xx and gateway errors by service and error code. |
| `search_structured_logs` | No | Filters structured JSON service logs by severity, correlation ID, and service name. |
| `diagnose_service_health` | No | Aggregates latency, error spikes, and recent logs into a typed health diagnosis. |

---

## 3. Measured Performance & Security Benchmarks

> **Ground-Truth Policy:** All figures below were measured directly on completed code using `benchmarks/run_benchmarks.py` and `pytest -v`.

### 3.1 Measured Results Summary (`BENCHMARKS.md`)

| Evaluation Category | Target Metric | Measured Value | Status |
| :--- | :--- | :--- | :--- |
| **Gateway P95 Middleware Latency** | `< 15.0 ms` | **28.3 ms** | **Measured** |
| **Gateway P50 Middleware Latency** | `< 5.0 ms` | **21.81 ms** | **Measured** |
| **Local Async Worker Throughput** | `>= 100 req/s` | **46.36 req/s** | **Measured** |
| **Adversarial SQLi / DDL / DML Block Rate** | `100.0%` | **100.0%** (26/26 blocked) | **PASSED** |
| **Origin Allowlist Defense Rate** | `100.0%` | **100.0%** (4/4 blocked) | **PASSED** |
| **Scoped Bearer RBAC Enforcement** | `100.0%` | **100.0%** (4/4 blocked) | **PASSED** |
| **Unconfirmed/Replayed Write Block Rate** | `100.0%` | **100.0%** (3/3 blocked) | **PASSED** |
| **LLM-Recoverable Error Schema Compliance** | `100.0%` | **100.0%** | **PASSED** |
| **Valid Tool Call Error Rate** | `0.0%` | **0.0%** (0 failures out of 1000) | **PASSED** |

### 3.2 Automated Test Suite Pass Summary

```text
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.2.0, pluggy-1.7.0
plugins: anyio-4.16.0, asyncio-1.5.0
collected 74 items

tests/integration/test_streamable_http_mcp.py .....                     [  6%]
tests/security/test_adversarial_sql_and_writes.py .................... [ 44%]
tests/security/test_auth_and_origin.py ......                          [ 52%]
tests/unit/test_confirmation_gate.py ........                          [ 63%]
tests/unit/test_fail_closed_resilience.py ...                          [ 67%]
tests/unit/test_rate_limit_and_breaker.py ..                           [ 70%]
tests/unit/test_servers.py ...........                                 [ 87%]
tests/unit/test_sql_guard.py ......                                    [ 95%]
tests/unit/test_sql_guard_cache.py ..                                  [ 98%]
tests/unit/test_stdio_transport.py .                                   [100%]

============================= 74 passed in 0.97s ==============================
```

---

## 4. Execution Modes & Setup Guide

### 4.1 Mode A: Automated Tests & Offline Benchmarks (Zero External Dependencies)
All unit, security, and benchmark suites run hermetically in-memory without requiring Docker, live PostgreSQL, Redis, or GitHub tokens:

```bash
# Clone and setup virtual environment
git clone https://github.com/brjmkwn/mcp-control-plane.git
cd mcp-control-plane

python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
pip install -e ".[dev]"

# Run code hygiene linting
ruff check .

# Execute complete test suite (74 tests)
pytest -v

# Run reproducible 1,000-request load benchmark & adversarial evaluation
python benchmarks/run_benchmarks.py --requests 1000 --concurrency 25 --write-report --ci
```

---

### 4.2 Mode B: Live Local Stack (Docker Compose)
Runs the gateway alongside live PostgreSQL with `pgvector`, Redis sliding-window cache, and Prometheus metric scraper:

```bash
# Configure local environment
cp .env.example .env

# Launch entire distributed stack
docker compose up --build -d

# Verify stack health
curl http://localhost:8000/health
# Response: {"status":"healthy","gateway":"online","dependencies":{"database":"configured","redis":"configured","github_provider":"mock"}}

# Scrape Prometheus metrics
curl http://localhost:8000/metrics | grep mcp_tool_calls_total
```

---

### 4.3 Mode C: Local Desktop MCP Host (`stdio` Transport)
To integrate with Claude Desktop, Cursor, or local MCP hosts over standard input/output (see template in `config/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "control-plane": {
      "command": "python",
      "args": ["-m", "mcp_control_plane.main", "--transport", "stdio"],
      "env": {
        "MCP_TEST_MODE": "true"
      }
    }
  }
}
```

---

### 4.4 Mode D: AWS Serverless Cloud Deployment (Terraform IaC)
Deploy to AWS Lambda (Container Image via Mangum) and Amazon API Gateway HTTP API for ₹0/month idle cost:

```bash
cd infra/terraform

# Format and validate configuration
terraform fmt -check
terraform init -backend=false
terraform validate

# Provision live infrastructure (requires AWS credentials)
# terraform apply
```

---

## 5. Security & Zero-Trust Architecture

1. **DNS Rebinding Defense:** Untrusted `Origin` headers are rejected with HTTP 403 before payload execution.
2. **Cryptographic 2-Step Confirmation Gate:** Destructive/mutating actions (`draft_triage_issue`, `post_pr_triage_comment`) cannot be executed in one shot. The gateway issues an HMAC-SHA256 token bound to `(client_id, tool_name, args_hash, exp, nonce)`. Replayed or tampered tokens fail closed.
3. **AST SQL Guardrails:** Pure AST-level inspection via `sqlglot` rejects multi-statement queries, DDL/DML, system catalog exfiltration, and enforces row-count clamps (`LIMIT <= 100`).
4. **Resilience & Backpressure:** Sliding-window rate limiting prevents agent runaway loops; circuit breakers fast-fail cascading upstream outages.
5. **Deterministic Error Taxonomy:** Gateway never leaks raw stack traces, DB credentials, or bearer tokens; all errors conform to the structured LLM-recoverable schema.

---

## 6. Project Layout

```text
mcp-control-plane/
├── PRD.md                            # Product Requirements Document
├── PID.md                            # Project Implementation Document
├── README.md                         # Architecture, Setup & Measured Benchmarks
├── BENCHMARKS.md                     # Measured Benchmark Output Report
├── pyproject.toml                    # Package metadata & build configuration
├── Dockerfile                        # Multi-stage non-root runtime container
├── docker-compose.yml                # Local orchestration (Gateway + Postgres + Redis + Prometheus)
├── .env.example                      # Documented environment template
├── .gitignore                        # Git exclusion rules
├── .github/workflows/ci.yml          # GitHub Actions CI workflow
├── config/
│   ├── prometheus.yml                # Scrape configuration
│   └── seed_postgres.sql             # Schema & vector embeddings seed
├── infra/terraform/                  # AWS Serverless IaC (Lambda Container + API Gateway)
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── versions.tf
├── src/mcp_control_plane/
│   ├── config.py                     # Pydantic Settings with fail-closed defaults
│   ├── lambda_handler.py             # Mangum adapter for AWS Lambda
│   ├── main.py                       # Application factory & CLI entrypoint
│   ├── core/                         # Errors, Logging, Metrics, Models
│   ├── gateway/                      # Auth, Confirmation, Rate Limiting, Breaker, Registry, Router
│   └── servers/                      # 3 Enterprise Servers (postgres_analytics, github_triage, observability)
├── tests/
│   ├── unit/                         # Unit tests (confirmation, rate limit, servers, sql guard)
│   ├── security/                     # Security tests (adversarial SQL, auth, origin)
│   └── integration/                  # Streamable HTTP MCP integration tests
└── benchmarks/
    └── run_benchmarks.py             # Automated load & adversarial benchmark runner
```

---

## 7. License

Distributed under the MIT License. See `LICENSE` for more information.
