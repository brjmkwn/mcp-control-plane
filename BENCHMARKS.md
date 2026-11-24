# Measured Performance & Security Benchmarks: `mcp-control-plane`

**Generated:** 2026-11-24T16:46:33.714541+00:00
**Hardware & Environment:**
- **Operating System:** Windows 11 (AMD64)
- **Python Version:** 3.13.14
- **CPU Cores:** 20 (Intel64 Family 6 Model 154 Stepping 3, GenuineIntel)
- **Execution Mode:** In-Memory ASGI Streamable HTTP / Redis In-Memory Fallback

---

## 1. Executive Benchmark Summary

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

---

## 2. Throughput & Latency Profile (Suite A)

Measured over **1000** concurrent requests with concurrency pool **25** across all 13 tools:

```text
Requests Dispatched: 1000
Concurrency Level:   25
Total Wall Time:     21.572s
Throughput:          46.36 req/s
Success Rate:        100.0% (1000 successes, 0 errors)

Latency Distribution:
  Min:     10.58 ms
  P50:     21.81 ms
  P95:     28.3 ms
  P99:     36.22 ms
  Max:     90.26 ms
  Average: 21.5 ms
```

---

## 3. Adversarial Security Evaluation (Suite B)

Tested across 4 defense boundaries using automated adversarial attack generators:

### 3.1 SQL AST Guardrail Verification (`sqlglot`)
- **Total Payloads Tested:** 26
- **Categories Tested:** DDL `DROP`/`ALTER`/`CREATE`, DML `DELETE`/`UPDATE`/`INSERT`, `TRUNCATE`, stacked queries (`;`), CTE-wrapped mutations (`WITH ... DELETE`), system catalog access (`pg_catalog`, `pg_shadow`, `pg_authid`), and unsafe functions (`pg_sleep`, `pg_read_file`, `dblink`, `lo_import`).
- **Bypasses Detected:** 0
- **Block Rate:** **100.0%**

### 3.2 DNS-Rebinding & Origin Enforcement
- **Requests Evaluated:** 4 untrusted browser origins
- **Block Rate:** **100.0%** (HTTP 403 Forbidden with `ORIGIN_DENIED`)

### 3.3 Scoped Bearer Token RBAC
- **Cross-Scope Escalation Attempts:** 4
- **Block Rate:** **100.0%** (HTTP 403 Forbidden with `FORBIDDEN_SCOPE`)

### 3.4 2-Step Confirmation Gate & Replay Defense
- **Attacks Tested:** Unconfirmed writes, altered argument tampering, forged HMAC signatures, and single-use nonce replay attacks.
- **Block Rate:** **100.0%** (0 unauthorized writes executed)

---

## 4. Resilience & Error Envelope Verification (Suite C)

- **Error Schema Compliance:** **100.0%**
- **Schema Format:**
```json
{
  "error": {
    "code": "...",
    "message": "...",
    "retryable": true|false,
    "details": { ... },
    "suggestions": [ "..." ]
  }
}
```
- **Guarantees:** Zero Python tracebacks, raw secrets, or unhandled exceptions leaked to LLM clients.

---

## 5. Exact Reproduction Command

```bash
python benchmarks/run_benchmarks.py --requests 1000 --concurrency 25 --write-report
```
