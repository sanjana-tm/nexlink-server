# ─────────────────────────────────────────────────────────────────────────────
# NexLink Server — Production Dockerfile
# Multi-stage build: dependencies → production image
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Build dependencies ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build deps for asyncpg + cryptography compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into /install for copy to final stage
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Production runtime ───────────────────────────────────────────────
FROM python:3.12-slim AS production

# Create non-root user for security
RUN groupadd --gid 1001 nexlink \
    && useradd --uid 1001 --gid nexlink --shell /bin/bash --create-home nexlink

WORKDIR /app

# Install only runtime system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=nexlink:nexlink . .

# Create writable dirs for logs and data
RUN mkdir -p /app/logs && chown -R nexlink:nexlink /app/logs

USER nexlink

# Expose server port
EXPOSE 9000

# Health check — hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:9000/health', timeout=5).raise_for_status()"

# Start server via uvicorn
# For production with multiple workers, override CMD in docker-compose
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000", "--log-level", "info"]
