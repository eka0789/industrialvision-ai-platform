# Multi-stage build for IndustrialVision AI FastAPI Backend
FROM python:3.12-slim AS builder

WORKDIR /build

# Install system build dependencies for OpenCV and compiled packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final production stage
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install runtime dependencies for OpenCV & headless execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Security: run as non-root user
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/bash -m appuser

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source code
COPY --chown=appuser:appuser . /app

# Copy and make entrypoint executable
COPY --chown=appuser:appuser docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Ensure directories for evidence and models exist with proper ownership
RUN mkdir -p /app/data/evidence /app/models /app/data/samples && \
    chown -R appuser:appuser /app/data /app/models

USER appuser

ENV HOST=0.0.0.0 \
    PORT=8000 \
    APP_ENV=production \
    PYTHONUNBUFFERED=1 \
    MIGRATE_ON_START=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
