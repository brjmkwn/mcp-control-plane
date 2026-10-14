# Project Implementation Document (PID): `mcp-control-plane`

**Purpose:** Step-by-step execution plan for building, verifying, benchmarking, and packaging `mcp-control-plane` from scratch. Designed to be executed sequentially by an engineer or an AI coding harness (Codex / Claude Code / Antigravity) with hard verification gates between milestones.

---

## 1. Target Repository Structure

```text
mcp-control-plane/
├── PRD.md
├── PID.md
├── README.md
├── BENCHMARKS.md                     # Generated ONLY from real benchmark runs
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── config/
│   ├── prometheus.yml
│   └── seed_postgres.sql
├── infra/
│   └── terraform/
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── versions.tf
├── src/
│   └── mcp_control_plane/
│       ├── __init__.py
│       ├── main.py                   # FastAPI app + CLI entrypoint (http / stdio)
│       ├── lambda_handler.py         # Mangum ASGI adapter for AWS Lambda
│       ├── config.py                 # Pydantic Settings (env-driven, fail-closed defaults)
│       ├── core/
│       │   ├── errors.py             # Structured LLM-recoverable error taxonomy
│       │   ├── logging.py            # structlog JSON audit logger + secret redaction
│       │   ├── metrics.py            # Prometheus counters, histograms, gauges
│       │   └── models.py             # JSON-RPC 2.0 + MCP ToolDefinition / ToolResult models
│       ├── gateway/
│       │   ├── auth.py               # Origin validator + Scoped Bearer RBAC
│       │   ├── confirmation.py       # HMAC-SHA256 2-step confirmation gate + nonce replay store
│       │   ├── rate_limit.py         # Redis sliding-window limiter + async in-memory fallback
│       │   ├── circuit_breaker.py    # Async circuit breaker (CLOSED/OPEN/HALF_OPEN)
│       │   ├── registry.py           # Unified MCP tool registry + middleware execution pipeline
│       │   └── router.py             # POST /mcp (JSON-RPC + SSE), GET /health, GET /metrics
│       └── servers/
│           ├── postgres_analytics/
│           │   ├── sql_guard.py      # sqlglot AST validator + LIMIT rewriter
│           │   ├── store.py          # Async Postgres/pgvector + SQLite/in-memory CI store
│           │   └── tools.py          # 4 analytics & hybrid search MCP tools
│           ├── github_triage/
│           │   ├── provider.py       # LiveGitHubProvider + DeterministicMockGitHubProvider
│           │   └── tools.py          # 5 CI/incident triage MCP tools (2 write-gated)
│           └── observability/
│               ├── telemetry.py      # Prometheus/structured log query provider + seeded fixtures
│               └── tools.py          # 4 latency/error/log/health diagnostic tools
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_sql_guard.py
│   │   ├── test_confirmation_gate.py
│   │   ├── test_rate_limit_and_breaker.py
│   │   └── test_servers.py
│   ├── security/
│   │   ├── test_auth_and_origin.py
│   │   └── test_adversarial_sql_and_writes.py
│   └── integration/
│       └── test_streamable_http_mcp.py
└── benchmarks/
    └── run_benchmarks.py             # Concurrent load + security eval runner -> BENCHMARKS.md
```

---

## 2. Execution Rules for Implementation

1. **Sequential Milestone Gating:** Do not advance to Milestone $N+1$ until all verification commands for Milestone $N$ exit with code `0`.
2. **No Stubs / No Placeholders:** Every tool and middleware must execute real logic. Do not leave empty `pass` functions or fake hardcoded benchmark tables.
3. **Zero Tracked Secrets:** Never commit `.env` or real tokens. Use `.env.example` with dummy placeholder values only.
4. **Cross-Platform Hygiene:** Never hardcode Windows paths (`C:\Users\...`) anywhere in source code, tests, scripts, or Markdown files.

---

## 3. Step-by-Step Implementation Plan

### Milestone 0: Repository Scaffolding & Tooling
* **Step 01:** Initialize Git repository and create the exact folder tree shown in Section 1.
* **Step 02:** Create `.gitignore` covering `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.terraform/`, `*.tfstate*`, and local SQLite files.
* **Step 03:** Author `pyproject.toml` with Python `>=3.12`, dependencies (`fastapi`, `uvicorn`, `pydantic>=2`, `pydantic-settings`, `httpx`, `sqlglot`, `structlog`, `prometheus-client`, `redis`, `asyncpg`, `mangum`, `mcp`), dev dependencies (`pytest`, `pytest-asyncio`, `ruff`), and `ruff` lint/format rules.
* **Step 04:** Create `.env.example` documenting all environment variables (`MCP_ALLOWED_ORIGINS`, `MCP_TOKENS_JSON`, `MCP_CONFIRMATION_SECRET`, `DATABASE_URL`, `REDIS_URL`, `GITHUB_TOKEN`, `GITHUB_PROVIDER_MODE`, `PROMETHEUS_URL`).
* **Step 05:** Create virtual environment and install package in editable mode (`pip install -e ".[dev]"`).
* **Exit Gate M0:** `python -c "import mcp_control_plane"` and `ruff check .` pass with exit code `0`.

---

### Milestone 1: Core Models, Config, Errors, Audit Logging & Metrics
* **Step 06:** Implement `config.py` using `pydantic-settings` with fail-closed defaults (empty allowlist/no wildcard admin tokens unless explicitly configured; test mode flag clearly separated).
* **Step 07:** Implement `core/errors.py` defining typed exception classes (`AuthError`, `OriginDeniedError`, `ScopeDeniedError`, `ValidationFailedError`, `GuardrailBlockedError`, `ConfirmationRequiredError`, `RateLimitExceededError`, `ConcurrencyLimitError`, `CircuitOpenError`, `UpstreamError`) and a serializer producing structured LLM-recoverable JSON (`code`, `message`, `retryable`, `details`, `suggestions`).
* **Step 08:** Implement `core/logging.py` configuring `structlog` JSON rendering with an automatic redaction processor that masks `Authorization`, `token`, `secret`, `password`, and `confirmation_token` fields.
* **Step 09:** Implement `core/metrics.py` registering Prometheus metrics:
  * `mcp_tool_calls_total` (Counter: `tool`, `server`, `status`, `error_code`)
  * `mcp_tool_duration_seconds` (Histogram: `tool`, `server`)
  * `mcp_guardrail_blocks_total` (Counter: `guardrail_type`, `tool`)
  * `mcp_circuit_breaker_state` (Gauge: `upstream` — `0=closed`, `1=open`, `2=half_open`)
* **Step 10:** Implement `core/models.py` defining Pydantic v2 models for JSON-RPC 2.0 requests/responses, `ToolSpec` (`name`, `server`, `description`, `input_model`, `required_scope`, `mutating`, `rate_limit_rpm`, `max_concurrency`, `upstream_name`), and `AuditRecord`.
* **Exit Gate M1:** `ruff check src/` passes; importing all `core/*` modules succeeds with zero warnings.

---

### Milestone 2: Production MCP Gateway Middleware & Transport
* **Step 11:** Implement `gateway/auth.py`:
  * `validate_origin(origin, allowed_origins)` raising `OriginDeniedError` (HTTP 403) on untrusted origins.
  * `authenticate_bearer(auth_header, token_store)` returning `ClientPrincipal(client_id, scopes)` or raising `AuthError` (HTTP 401).
  * `require_scope(principal, required_scope)` raising `ScopeDeniedError` (HTTP 403) when missing.
* **Step 12:** Implement `gateway/confirmation.py`:
  * Canonical JSON hashing of tool arguments (excluding `confirmation_token`).
  * `issue_token(client_id, tool_name, args_hash)` signing `(client_id, tool_name, args_hash, exp, nonce)` with HMAC-SHA256.
  * `verify_and_consume_token(...)` checking signature, expiry TTL (default 120s), exact `client_id` + `tool_name` + `args_hash` match, and marking `nonce` as consumed in Redis/in-memory store to block replay attacks.
* **Step 13:** Implement `gateway/rate_limit.py`:
  * Async `RateLimiter` protocol with `RedisSlidingWindowRateLimiter` (sorted set per `client_id:tool_name`) and `InMemorySlidingWindowRateLimiter` fallback.
  * Returns `(allowed: bool, remaining: int, retry_after_ms: int)`.
* **Step 14:** Implement `gateway/circuit_breaker.py`:
  * Async state machine (`CLOSED` -> `OPEN` after `failure_threshold` consecutive upstream failures -> `HALF_OPEN` after `recovery_timeout_seconds` -> `CLOSED` on probe success).
  * Updates `mcp_circuit_breaker_state` Prometheus gauge on every transition.
* **Step 15:** Implement `gateway/registry.py`:
  * Central `ToolRegistry` managing registered tools, per-tool `asyncio.Semaphore` pools, and executing the ordered 7-step middleware pipeline (`RBAC -> Rate Limit -> Semaphore -> Pydantic Validation -> 2-Step Confirmation Gate -> Circuit Breaker + Handler -> Audit Log & Metrics`).
* **Step 16:** Implement `gateway/router.py` and `main.py`:
  * `POST /mcp` handling JSON-RPC `initialize`, `tools/list` (scope-filtered), and `tools/call`. Return `text/event-stream` SSE format if `Accept: text/event-stream` is requested, else `application/json`.
  * `GET /health` and `GET /metrics`.
  * CLI entrypoint supporting `--transport http` (Uvicorn) and `--transport stdio` (line-delimited JSON-RPC over stdin/stdout).
* **Step 17:** Implement `lambda_handler.py` wrapping the FastAPI app with `Mangum(app, lifespan="off")` for AWS Lambda Container deployment.
* **Step 18:** Write unit tests `tests/unit/test_confirmation_gate.py`, `tests/unit/test_rate_limit_and_breaker.py`, and `tests/security/test_auth_and_origin.py`.
* **Exit Gate M2:** `pytest -v tests/unit/test_confirmation_gate.py tests/unit/test_rate_limit_and_breaker.py tests/security/test_auth_and_origin.py` passes 100%.

---

### Milestone 3: MCP Server 1 — `postgres_analytics_mcp`
* **Step 19:** Implement `servers/postgres_analytics/sql_guard.py` using `sqlglot`:
  * Parse strictly with dialect `postgres`; reject syntax errors and multi-statement queries (`len != 1`).
  * Require root expression `isinstance(node, exp.Select)`.
  * Walk AST and block all mutating/DDL/DML nodes (`Insert`, `Update`, `Delete`, `Drop`, `Alter`, `Create`, `Command`, `Copy`, `Grant`, `Revoke`, `TruncateTable`, `Into`, `Lock`) and unsafe functions (`pg_sleep`, `pg_read_file`, `lo_import`, `dblink`).
  * Block references to forbidden system tables/schemas (`pg_catalog`, `pg_shadow`, `pg_authid`, `pg_roles`).
  * Automatically clamp/inject `LIMIT <= 100` on the AST and regenerate normalized SQL.
* **Step 20:** Create `config/seed_postgres.sql` creating `pgvector` extension, sample operational tables (`services`, `deployments`, `incidents`, `runbook_docs` with vector column), and seeded deterministic rows.
* **Step 21:** Implement `servers/postgres_analytics/store.py` supporting:
  * `PostgresAnalyticsStore` (`asyncpg` with read-only transaction + `SET LOCAL statement_timeout = '3000ms'`).
  * `SQLiteFallbackAnalyticsStore` (in-memory SQLite seeded with identical sample tables + deterministic TF/cosine vector similarity over `runbook_docs` so CI and local tests run cleanly without Docker).
* **Step 22:** Implement `servers/postgres_analytics/tools.py` registering `list_tables`, `describe_schema`, `run_readonly_query`, and `hybrid_search_docs` under scope `read:db`.
* **Step 23:** Write `tests/unit/test_sql_guard.py` and `tests/security/test_adversarial_sql_and_writes.py` testing at least **25 adversarial SQL payloads** (DDL, DML, stacked queries, CTE-wrapped `DELETE`, comment obfuscation, `pg_sleep`, `pg_shadow`, `LIMIT 999999` clamping).
* **Exit Gate M3:** `pytest -v tests/unit/test_sql_guard.py tests/security/test_adversarial_sql_and_writes.py` passes with `100%` adversarial block rate.

---

### Milestone 4: MCP Servers 2 & 3 — `github_triage_mcp` & `observability_mcp`
* **Step 24:** Implement `servers/github_triage/provider.py`:
  * `LiveGitHubProvider`: Async `httpx` client calling GitHub REST API v3 (`/repos/{owner}/{repo}/actions/runs`, `/commits/{sha}`, `/issues`, `/issues/{num}/comments`) with timeout handling and status mapping so 5xx errors trigger the circuit breaker.
  * `DeterministicMockGitHubProvider`: Seeded realistic CI failure logs, commit diffs, open incidents, and in-memory created issues/comments for automated testing and offline benchmarks.
* **Step 25:** Implement `servers/github_triage/tools.py` registering:
  * Read tools (`read:github`): `get_failed_workflow_logs`, `get_commit_diff`, `list_open_incidents`.
  * Write tools (`write:github`, `mutating=True`): `draft_triage_issue`, `post_pr_triage_comment` (strictly protected by the 2-step HMAC confirmation gate).
* **Step 26:** Implement `servers/observability/telemetry.py` supporting both live Prometheus HTTP API queries (`PROMETHEUS_URL`) and a seeded deterministic telemetry/log store for local/CI operation.
* **Step 27:** Implement `servers/observability/tools.py` registering `query_latency_anomalies`, `get_error_breakdown`, `search_structured_logs`, and `diagnose_service_health` under scope `read:ops`.
* **Step 28:** Wire all 13 tools from all 3 servers into `main.py` via `build_app()`.
* **Step 29:** Write `tests/unit/test_servers.py` and `tests/integration/test_streamable_http_mcp.py` exercising end-to-end `POST /mcp` calls (JSON and SSE modes), `tools/list` scope filtering, 2-step write flows, rate limiting, concurrency semaphore backpressure, circuit-breaker trip/recovery, and `/metrics` verification.
* **Exit Gate M4:** `pytest -v` runs the entire suite across all 3 servers and gateway middleware with `0` failures and `0` errors.

---

### Milestone 5: Reproducible Load & Security Benchmarks (`BENCHMARKS.md`)
* **Step 30:** Implement `benchmarks/run_benchmarks.py` supporting `--requests 1000 --concurrency 25` and `--ci` flags against the live ASGI gateway (`httpx.AsyncClient(transport=ASGITransport(app=app))` or live HTTP URL):
  * **Suite A (Throughput & Latency):** Sends 1,000 concurrent valid tool calls across all 3 servers; computes exact `P50`, `P95`, `P99` latency (ms) and throughput (`req/s`).
  * **Suite B (Adversarial Security Eval):** Executes >= 25 SQLi/DDL/DML payloads, unauthorized RBAC calls, untrusted `Origin` requests, and unconfirmed/tampered/replayed write calls; records exact block rates (`%`).
  * **Suite C (Resilience Verification):** Triggers rate-limit cap and circuit-breaker trip/recovery; records exact structured error compliance (`%`).
* **Step 31:** Add a `--write-report` flag that writes real measured hardware/environment metadata, exact command run, and measured tables into `BENCHMARKS.md`.
* **Step 32:** Execute `python benchmarks/run_benchmarks.py --requests 1000 --concurrency 25 --write-report` and verify all guardrail block rates equal `100.0%` and error rate on valid calls is `0.0%`.
* **Exit Gate M5:** `BENCHMARKS.md` exists with 100% real measured numbers and zero placeholder strings (`TBD`, `<X>`).

---

### Milestone 6: Docker, AWS Terraform IaC, GitHub Actions CI & Documentation
* **Step 33:** Write multi-stage non-root `Dockerfile` (`python:3.12-slim`, UID `1000`, `HEALTHCHECK` on `/health`).
* **Step 34:** Write `docker-compose.yml` and `config/prometheus.yml` connecting `mcp-gateway`, `postgres` (`pgvector`), `redis`, and `prometheus`.
* **Step 35:** Author AWS Terraform configuration in `infra/terraform/`:
  * `versions.tf`: Terraform `>= 1.6`, `hashicorp/aws ~> 5.0`.
  * `variables.tf`: `aws_region` (default `ap-south-1`), `project_name`, `image_tag`, `allowed_origins`.
  * `main.tf`: `aws_ecr_repository`, `aws_cloudwatch_log_group`, least-privilege `aws_iam_role` + `aws_iam_role_policy_attachment`, `aws_lambda_function` (`package_type = "Image"`), `aws_apigatewayv2_api` (HTTP API) + integration + routes (`POST /mcp`, `GET /health`) + stage, and `aws_lambda_permission`.
  * `outputs.tf`: `ecr_repository_url`, `api_gateway_endpoint`, `lambda_function_name`.
* **Step 36:** Author `.github/workflows/ci.yml` running `ruff check`, `ruff format --check`, `pytest -v`, `python benchmarks/run_benchmarks.py --ci`, `docker build .`, and `terraform fmt -check && terraform init -backend=false && terraform validate`.
* **Step 37:** Write recruiter-ready `README.md` containing:
  * Clean ASCII architecture diagram.
  * Feature & 13-tool summary table across all 3 MCP servers.
  * Strict separation of **Live Execution (Docker Compose & AWS Terraform Deployment)** vs. **Automated Tests & Offline Benchmarks (`pytest` / `run_benchmarks.py`)**.
  * Exact measured benchmark table copied verbatim from `BENCHMARKS.md` + exact `pytest` pass summary.
* **Step 38:** Run final verification sweep:
  * `ruff check .`
  * `pytest -v`
  * `python benchmarks/run_benchmarks.py --ci`
  * `docker build -t mcp-control-plane:local .` (if Docker daemon is running)
  * `terraform fmt -check && terraform init -backend=false && terraform validate` inside `infra/terraform/` (if Terraform CLI is installed)
* **Exit Gate M6:** All checks pass cleanly; `git status` shows zero untracked `.env` or secret files; ready for final user review and `git push`.
