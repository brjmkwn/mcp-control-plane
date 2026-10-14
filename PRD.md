# Product Requirements Document (PRD): `mcp-control-plane`

**Project Name:** `mcp-control-plane` — Zero-Trust Production MCP Gateway & Enterprise Multi-Server Suite  
**Version:** 1.0  
**Status:** Approved for Implementation  
**Owner:** Brij Makwana  
**Target Stack:** Python 3.12+, FastAPI, MCP SDK / FastMCP, Pydantic v2, `sqlglot`, PostgreSQL (`pgvector`), Redis, Prometheus, Docker, Terraform (AWS), GitHub Actions  

---

## 1. Executive Overview & Problem Statement

Model Context Protocol (MCP) makes it straightforward to connect LLM agents to external tools locally over `stdio`, but deploying MCP servers in production enterprise environments introduces severe operational and security gaps:

1. **Unauthenticated & Over-Privileged Tool Execution:** Local MCP servers expose read and destructive write tools under a single implicit trust boundary without per-client RBAC or human/agent confirmation gates.
2. **Unsafe Data Access:** Exposing SQL databases directly to LLM-generated queries risks DDL/DML mutations (`DROP`, `DELETE`), stacked-query SQL injection, full-table scans, and system-catalog exfiltration.
3. **Lack of Resilience & Observability:** Raw MCP tool handlers lack per-tool rate limiting, concurrency bounds, upstream circuit breakers, structured LLM-recoverable errors, and Prometheus latency/error telemetry.
4. **No Standardized Cloud Deployment Path:** Teams struggle to deploy stateless Streamable HTTP MCP services behind cloud API gateways with reproducible Infrastructure-as-Code (IaC) and automated CI security gates.

**Solution:** `mcp-control-plane` is a reusable, production-hardened MCP gateway and three-domain enterprise tool suite that enforces zero-trust transport security, scoped RBAC, AST-level SQL guardrails, two-step confirmation gates for write actions, Redis-backed resilience controls, Prometheus telemetry, and AWS serverless deployment via Terraform.

---

## 2. Goals, Non-Goals & Ground-Truth Verification Policy

### 2.1 Core Goals
* Expose a **stateless Streamable HTTP** MCP endpoint (`POST /mcp`) compliant with JSON-RPC 2.0 and SSE streaming, alongside a local `stdio` development mode.
* Enforce defense-in-depth middleware: `Origin` allowlisting, scoped Bearer token RBAC, strict Pydantic v2 argument validation, and HMAC-signed two-step confirmation tokens for all mutating tools.
* Ship **three real enterprise MCP tool domains** (Database Analytics, GitHub CI/Incident Triage, and Service Observability) runnable both standalone and mounted behind the unified gateway.
* Provide deterministic, reproducible unit, integration, adversarial security, and load benchmark suites with zero external paid API dependency required to run CI.
* Package the entire stack with a non-root multi-stage Dockerfile, local `docker-compose.yml`, AWS Terraform IaC (`infra/terraform/`), and GitHub Actions CI.

### 2.2 Explicit Non-Goals
* Building a custom web frontend or chat UI (clients connect via standard MCP hosts such as Claude Desktop, Cursor, or programmatic MCP/HTTP clients).
* Implementing full OAuth2 Identity Provider (IdP) user management; scoped signed Bearer tokens / API keys mapped to RBAC policies are sufficient for service-to-service MCP governance.
* Claiming live AWS deployment metrics before `terraform apply` is actually executed.

### 2.3 Strict Ground-Truth Verification Policy (Anti-Trap Rule)
> **MANDATORY:** All numeric targets in Section 5 of this PRD are **design targets only**. No metric from `PRD.md` may be copied into `README.md`, `BENCHMARKS.md`, or a resume until `benchmarks/run_benchmarks.py` and `pytest` execute against completed production code and record real measured outputs.

---

## 3. System Architecture

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

### Request Execution Lifecycle
1. Client sends `POST /mcp` with a JSON-RPC 2.0 payload (`tools/list` or `tools/call`).
2. **Origin Validator** verifies the `Origin` header against an explicit allowlist (missing `Origin` allowed only for non-browser CLI/server clients if configured; untrusted origins fail closed with `403`).
3. **RBAC Middleware** authenticates the Bearer token, resolves `client_id` and granted scopes, and rejects unauthorized tools with `401`/`403` before handler execution.
4. **Rate Limiter & Semaphore** enforce per-client/per-tool RPM limits and max concurrent executions, returning structured retryable errors (`RATE_LIMITED` / `CONCURRENCY_LIMIT`) with `retry_after_ms`.
5. **Input Validator & Confirmation Gate** validate arguments via Pydantic v2. If the tool is marked `mutating=True`, the gate verifies a short-lived, single-use HMAC-SHA256 `confirmation_token` bound to `(client_id, tool_name, canonical_args_hash)`. If absent, it returns a dry-run preview and a newly minted token without executing side effects.
6. **Circuit Breaker** wraps the upstream tool call. Failures open the breaker and return a structured LLM-recoverable JSON payload rather than raw stack traces.
7. **Audit Logger & Metrics** emit a single structured JSON log record and update Prometheus counters/histograms.

---

## 4. Functional Requirements

### 4.1 Gateway Core (`src/gateway/`)

| ID | Requirement | Specification |
| :--- | :--- | :--- |
| **FR-GW-01** | Stateless Streamable HTTP | Implement `POST /mcp` supporting JSON-RPC 2.0 `initialize`, `tools/list`, and `tools/call`. Support both direct `application/json` responses and `text/event-stream` (SSE) when requested via `Accept: text/event-stream`. |
| **FR-GW-02** | Dual Transport Mode | Support `--transport http` (production default) and `--transport stdio` (local MCP host development) sharing the exact same tool registry and guardrail pipeline. |
| **FR-GW-03** | Origin Validation | Reject requests with non-allowlisted `Origin` headers with HTTP `403 Forbidden` to prevent browser-based DNS rebinding attacks. |
| **FR-GW-04** | Scoped Bearer RBAC | Map Bearer tokens (loaded from environment/config) to client identities and explicit scopes (`read:db`, `read:github`, `write:github`, `read:ops`, `admin`). `tools/list` filters or annotates visibility; `tools/call` fails closed (`401`/`403`). |
| **FR-GW-05** | 2-Step Confirmation Gate | All write tools require a valid `confirmation_token`. Step 1 (no token): returns `{status: "confirmation_required", preview: {...}, confirmation_token: "<hmac>", expires_in_seconds: 120}`. Step 2 (valid token): verifies signature, TTL, client/args hash match, marks nonce consumed (replay prevention), and executes. |
| **FR-GW-06** | Rate Limiting & Backpressure | Sliding-window rate limiter backed by Redis (`ZREMRANGEBYSCORE` / `ZADD`) with seamless async in-memory fallback for unit tests/CI. Per-tool `asyncio.Semaphore` prevents slow upstream calls from exhausting worker pools. |
| **FR-GW-07** | Circuit Breaker | Per-upstream breaker (`CLOSED`, `OPEN`, `HALF_OPEN`) with configurable failure threshold and cooldown window. |
| **FR-GW-08** | LLM-Recoverable Errors | All gateway and tool errors return a deterministic JSON schema: `{"error": {"code": "...", "message": "...", "retryable": bool, "details": {...}, "suggestions": ["..."]}}`. Never leak raw Python tracebacks or secrets. |
| **FR-GW-09** | Observability Endpoints | Expose `GET /metrics` (Prometheus text format: `mcp_tool_calls_total`, `mcp_tool_duration_seconds`, `mcp_guardrail_blocks_total`, `mcp_circuit_breaker_state`) and `GET /health` (dependency readiness). |

### 4.2 Enterprise MCP Server 1: `postgres_analytics_mcp` (`src/servers/postgres_analytics/`)

| Tool Name | Required Scope | Mutating | Description |
| :--- | :--- | :--- | :--- |
| `list_tables` | `read:db` | No | Lists accessible public/analytics tables with row-count estimates. |
| `describe_schema` | `read:db` | No | Returns column names, types, nullability, and indexes for a validated table name. |
| `run_readonly_query` | `read:db` | No | Executes an AST-validated read-only SQL query with auto-enforced row limit (`<= 100`) and statement timeout (`<= 3000 ms`). |
| `hybrid_search_docs` | `read:db` | No | Performs hybrid keyword + dense vector similarity search over seeded operational runbooks (`pgvector` in Postgres; deterministic cosine + lexical fallback in SQLite/in-memory CI mode). |

**Mandatory SQL AST Guardrail Rules (`sqlglot`):**
* Parse query strictly with `sqlglot.parse(sql, read="postgres")`. Reject parse errors.
* Reject multi-statement / stacked queries (`len(statements) != 1`).
* Root AST node MUST be `exp.Select` (which permits safe `WITH ... SELECT` CTEs).
* Walk the entire AST tree and reject any occurrence of `Insert`, `Update`, `Delete`, `Drop`, `Alter`, `Create`, `Command`, `Copy`, `Grant`, `Revoke`, `TruncateTable`, or dangerous Postgres functions (`pg_sleep`, `pg_read_file`, `lo_import`, `dblink`).
* Block access to sensitive system tables/schemas (`pg_catalog`, `pg_shadow`, `pg_authid`, `pg_roles`).
* Automatically wrap or rewrite queries lacking a `LIMIT` (or exceeding `LIMIT 100`) to cap returned rows at `100`.

### 4.3 Enterprise MCP Server 2: `github_triage_mcp` (`src/servers/github_triage/`)

| Tool Name | Required Scope | Mutating | Description |
| :--- | :--- | :--- | :--- |
| `get_failed_workflow_logs` | `read:github` | No | Retrieves recent failed GitHub Actions job steps and truncated error excerpts for a repo/run. |
| `get_commit_diff` | `read:github` | No | Fetches changed files and diff hunks for a commit SHA (path-validated, size-capped). |
| `list_open_incidents` | `read:github` | No | Lists open issues filtered by label (e.g., `incident`, `bug`, `p0`). |
| `draft_triage_issue` | `write:github` | **Yes (2-Step)** | Creates a structured incident triage issue after confirmation token validation. |
| `post_pr_triage_comment` | `write:github` | **Yes (2-Step)** | Posts a root-cause analysis comment on a PR after confirmation token validation. |

**Provider Isolation Requirement:**
* Implement a clean `GitHubProvider` protocol with two concrete implementations:
  1. `LiveGitHubProvider` (`httpx.AsyncClient` using `GITHUB_TOKEN`, wrapped by the gateway circuit breaker).
  2. `DeterministicMockGitHubProvider` (explicitly scoped to automated tests and offline benchmarks; clearly separated in documentation).

### 4.4 Enterprise MCP Server 3: `observability_mcp` (`src/servers/observability/`)

| Tool Name | Required Scope | Mutating | Description |
| :--- | :--- | :--- | :--- |
| `query_latency_anomalies` | `read:ops` | No | Detects services whose P95 latency exceeds baseline thresholds over a lookback window. |
| `get_error_breakdown` | `read:ops` | No | Aggregates HTTP 4xx/5xx and tool error counts grouped by service and error code. |
| `search_structured_logs` | `read:ops` | No | Searches structured JSON service logs by service name, severity, correlation ID, and time range. |
| `diagnose_service_health` | `read:ops` | No | Combines latency anomalies, error spikes, and recent error logs into a typed Pydantic health summary. |

---

## 5. Non-Functional Requirements & Design Targets

*(All quantitative metrics below are **implementation targets** to be verified via `benchmarks/run_benchmarks.py`.)*

| Metric | Design Target | Verification Method |
| :--- | :--- | :--- |
| Gateway Middleware P95 Overhead | `< 15 ms` (in-process/local ASGI) | `benchmarks/run_benchmarks.py` (500–1,000 requests) |
| Throughput (Local Async Workers) | `>= 100 req/s` | Concurrent load runner in `benchmarks/run_benchmarks.py` |
| Adversarial SQLi / DDL / DML Block Rate | `100.0%` (0 bypasses across >= 20 payloads) | `tests/security/test_sql_guardrails.py` + benchmark eval |
| Unauthorized RBAC / Bad Origin Block Rate | `100.0%` (fail closed) | `tests/security/test_auth_and_origin.py` |
| Unconfirmed Write Tool Block Rate | `100.0%` (including tampered/replayed tokens) | `tests/security/test_confirmation_gate.py` |
| Lint & Static Hygiene | `0` `ruff` errors; `.env` gitignored; zero hardcoded local paths | `ruff check .` & CI checks |

---

## 6. Cloud Infrastructure, IaC & CI/CD Specification

### 6.1 Containerization & Local Stack
* **Dockerfile:** Multi-stage Python 3.12-slim image, non-root user (`appuser`, UID `1000`), minimal runtime layers, `HEALTHCHECK` hitting `/health`.
* **`docker-compose.yml`:** Orchestrates `mcp-gateway`, `postgres` (`pgvector/pgvector:pg16` with init seed SQL), `redis` (`redis:7-alpine`), and `prometheus` (`prom/prometheus` scraping `mcp-gateway:8000/metrics`).

### 6.2 AWS Serverless Infrastructure (`infra/terraform/`)
* Designed for **₹0/month idle cost** using AWS serverless primitives:
  * `aws_ecr_repository` for gateway container images.
  * `aws_lambda_function` (`package_type = "Image"`, Mangum ASGI adapter) **or** optional toggle for `aws_ecs_service` Fargate.
  * `aws_apigatewayv2_api` (HTTP API) routing `POST /mcp` and `GET /health`.
  * `aws_cloudwatch_log_group` with 14-day retention.
  * Least-privilege `aws_iam_role` and policy (ECR pull + CloudWatch Logs write only; no wildcard `*` admin permissions).
* Must pass `terraform fmt -check` and `terraform validate` cleanly without requiring live AWS credentials.

### 6.3 GitHub Actions CI (`.github/workflows/ci.yml`)
Runs automatically on every push and pull request:
1. `ruff check .` and `ruff format --check .`
2. `pytest -v` (unit + integration + adversarial security suite)
3. `python benchmarks/run_benchmarks.py --ci` (verifies guardrail block rates = `100.0%` and zero unhandled errors)
4. `docker build .` (verifies container build succeeds)
5. `terraform init -backend=false && terraform validate` inside `infra/terraform/`

---

## 7. Security Threat Model & Adversarial Test Matrix

| Threat Vector | Control Implemented | Required Automated Test Case |
| :--- | :--- | :--- |
| **DNS Rebinding** | Strict `Origin` allowlist check | Request with `Origin: https://evil.example` returns HTTP `403`. |
| **Privilege Escalation** | Scoped Bearer RBAC middleware | `read:db` token calling `draft_triage_issue` returns HTTP `403`. |
| **SQL Injection / Destruction** | `sqlglot` AST allowlist + read-only DB session | >= 20 adversarial queries (`DROP`, `DELETE`, `UNION` into `pg_shadow`, stacked `;`, comment obfuscation, `pg_sleep`, CTE with `DELETE`) blocked with `100%` accuracy. |
| **Unintended Agent Side Effects** | 2-Step HMAC confirmation gate + single-use nonce | Calling write tool without token, with expired token, with mutated args, with another client's token, or replaying a used token all fail closed. |
| **Agent Tool-Call Loop / DoS** | Per-tool sliding-window rate limiter + semaphore | Burst exceeding RPM returns structured `RATE_LIMITED` error with `retry_after_ms`. |
| **Cascading Upstream Failure** | Per-upstream circuit breaker | N consecutive upstream 503s trip breaker to `OPEN`; fast-fail structured error returned until cooldown elapses. |
| **Secret Leakage** | Error sanitizer + audit log redactor | Bearer tokens, `GITHUB_TOKEN`, and DB credentials never appear in HTTP errors or JSON logs. |

---

## 8. Acceptance Criteria (Definition of Done)

The project is complete and ready for portfolio publication only when **all** of the following pass:
1. All 13 MCP tools across all 3 servers are wired behind the unified Streamable HTTP gateway and callable via `POST /mcp`.
2. Full `pytest -v` suite passes with `0` failures and `0` skipped security tests.
3. `benchmarks/run_benchmarks.py` runs end-to-end and generates `BENCHMARKS.md` containing real measured P50/P95/P99 latency, throughput, and `100.0%` security/guardrail block rates.
4. `docker compose config` and `docker build .` succeed cleanly.
5. `terraform fmt -check` and `terraform validate` pass inside `infra/terraform/`.
6. `README.md` cleanly separates **Live Execution (Docker / AWS Terraform)** from **Automated Tests & Offline Benchmarks**, contains an ASCII architecture diagram, and shows exact reproduction commands and real measured outputs.
