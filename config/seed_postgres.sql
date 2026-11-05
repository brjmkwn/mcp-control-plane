-- PostgreSQL + pgvector initialization schema and seed data for mcp-control-plane

CREATE EXTENSION IF NOT EXISTS vector;

-- Services catalog table
CREATE TABLE IF NOT EXISTS services (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL,
    tier VARCHAR(16) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'healthy',
    owner_team VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Deployments audit table
CREATE TABLE IF NOT EXISTS deployments (
    id SERIAL PRIMARY KEY,
    service_id INT REFERENCES services(id),
    version VARCHAR(32) NOT NULL,
    environment VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    deployed_by VARCHAR(64) NOT NULL,
    deployed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Incidents log table
CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    service_id INT REFERENCES services(id),
    severity VARCHAR(8) NOT NULL,
    title VARCHAR(255) NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE
);

-- Operational Runbooks with dense vector embeddings (dim: 4 for deterministic testing)
CREATE TABLE IF NOT EXISTS runbook_docs (
    id SERIAL PRIMARY KEY,
    title VARCHAR(128) NOT NULL,
    category VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(4)
);

-- Seed deterministic records
INSERT INTO services (name, tier, status, owner_team) VALUES
    ('auth-service', 'tier-0', 'healthy', 'security-team'),
    ('payment-gateway', 'tier-0', 'healthy', 'payments-team'),
    ('order-fulfillment', 'tier-1', 'degraded', 'logistics-team'),
    ('notification-worker', 'tier-2', 'healthy', 'platform-team')
ON CONFLICT (name) DO NOTHING;

INSERT INTO deployments (service_id, version, environment, status, deployed_by) VALUES
    (1, 'v2.4.1', 'production', 'success', 'ci-bot'),
    (2, 'v1.8.0', 'production', 'success', 'brij-admin'),
    (3, 'v3.1.2', 'staging', 'failed', 'ci-bot'),
    (4, 'v1.0.5', 'production', 'success', 'ci-bot');

INSERT INTO incidents (service_id, severity, title, resolved) VALUES
    (3, 'P1', 'High order fulfillment processing latency spike', FALSE),
    (2, 'P2', 'Transient webhook callback drops from upstream processor', TRUE);

INSERT INTO runbook_docs (title, category, content, embedding) VALUES
    ('Payment Gateway Timeout Remediation', 'runbook', 'Steps to recover when payment gateway latency exceeds 5000ms: check upstream provider status, toggle fallback gateway.', '[0.91, 0.12, 0.35, 0.05]'),
    ('Database Connection Pool Starvation', 'runbook', 'Diagnosing connection pool exhaustion: inspect pg_stat_activity, scale pgbouncer max_client_conn, restart idle workers.', '[0.15, 0.88, 0.22, 0.41]'),
    ('Authentication Token Validation Failure', 'runbook', 'Remediation guide for sudden spike in 401s: verify public JWKS cache expiration and clock skew tolerance.', '[0.85, 0.08, 0.45, 0.11]'),
    ('Worker Queue Backlog Drain Protocol', 'runbook', 'How to drain Celery/Redis queue backlog during high throughput bursts: scale replicas and increase concurrency.', '[0.20, 0.30, 0.85, 0.35]');
