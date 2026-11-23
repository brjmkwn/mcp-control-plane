# Multi-stage production Dockerfile for mcp-control-plane

# Stage 1: Build & wheels preparation
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /build/
COPY src/ /build/src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels . && \
    pip install --no-cache-dir --target=/build/deps .

# Stage 2: Minimal non-root runtime image
FROM python:3.12-slim AS runner

WORKDIR /app

# Create non-root user (UID 1000)
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -s /bin/bash -m appuser

# Install curl for HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies and application
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY --from=builder /build/src /app/src
COPY pyproject.toml /app/

# Install application package in non-editable mode
RUN pip install --no-cache-dir --no-deps -e /app

USER appuser:appgroup

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_HOST="0.0.0.0" \
    MCP_PORT=8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["python", "-m", "mcp_control_plane.main", "--transport", "http"]
