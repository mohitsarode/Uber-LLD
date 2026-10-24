# Multi-stage Dockerfile for H3 Spatial Indexing & Dispatch Engine
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for ultra-fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml .
RUN uv venv /opt/venv && \
    uv pip install --no-cache-dir --prefix /opt/venv .

# Production Runtime Image
FROM python:3.12-slim AS runner

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Copy source code and dashboard static assets
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY load_test/ /app/load_test/
COPY pyproject.toml /app/

EXPOSE 8000

# Run FastAPI ASGI server with Uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
