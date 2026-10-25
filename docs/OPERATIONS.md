# IndustrialVision AI — Operations Guide

## Overview

IndustrialVision AI is a real-time defect detection and quality inspection platform. This document covers setup, database migrations, backup and restore procedures, RTSP in Docker deployment caveats, security best practices, and operational runbooks.

---

## 1. System Requirements & Architecture

- **Backend**: Python 3.12+ (FastAPI, SQLAlchemy, Alembic, OpenCV, ONNX Runtime)
- **Frontend**: Node 22+ (React, TypeScript, Vite, TailwindCSS)
- **Database**: PostgreSQL 16 (production) or SQLite (local development/unit testing)
- **Containerization**: Docker 25+ and Docker Compose v2+

```
               [ Internet / Factory LAN ]
                          │
                   :8080  ▼
            ┌───────────────────────────┐
            │   Frontend (Nginx-Alpine) │
            │      SPA Static Assets    │
            └─────────────┬─────────────┘
                          │ /api/ and /ws/
                   :8000  ▼
            ┌───────────────────────────┐
            │   Backend (FastAPI/Python)│
            └─────────────┬─────────────┘
                          │ :5432 (Internal Network Only)
                          ▼
            ┌───────────────────────────┐
            │   PostgreSQL 16 Database  │
            └───────────────────────────┘
```

---

## 2. Local Bare-Metal Setup

### 2.1 Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Or on Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Create initial admin user interactively
python -m app.cli create-admin

# Start development server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 2.2 Frontend Setup

```bash
cd frontend

# Install Node dependencies
npm install

# Start Vite dev server
npm run dev  # Serves on http://localhost:5173
```

---

## 3. Production Deployment with Docker Compose

### 3.1 Environment Configuration

Copy the template and supply secure secrets:

```bash
cp .env.example .env
```

Ensure the following variables are set with cryptographically strong values:
- `JWT_SECRET`: Random 32+ characters string (e.g. generated via `openssl rand -hex 32`).
- `POSTGRES_PASSWORD`: Strong password for PostgreSQL user.
- `APP_ENV`: Set to `production`.
- `CORS_ORIGINS`: Explicit comma-separated origins (e.g. `http://inspection.factory.local:8080`).

### 3.2 Launch Containers

```bash
# Validate compose file
docker compose config

# Build and start services in detached mode
docker compose up -d --build

# Inspect logs
docker compose logs -f backend
```

---

## 4. Database Migrations

Alembic manages schema evolution.

### 4.1 Apply Migrations
```bash
# Inside docker container
docker compose exec backend alembic upgrade head

# On bare-metal
cd backend && alembic upgrade head
```

### 4.2 Rollback Migration
```bash
docker compose exec backend alembic downgrade -1
```

### 4.3 Check Migration Status
```bash
docker compose exec backend alembic current
```

---

## 5. Backup & Disaster Recovery

### 5.1 Automated PostgreSQL Backup

```bash
#!/bin/bash
BACKUP_DIR="/var/backups/industrialvision"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$BACKUP_DIR"

docker compose exec -T postgres pg_dump -U iv_user industrialvision | gzip > "$BACKUP_DIR/db_$TIMESTAMP.sql.gz"
echo "Backup created at $BACKUP_DIR/db_$TIMESTAMP.sql.gz"
```

### 5.2 Evidence Storage Backup

```bash
tar -czf "$BACKUP_DIR/evidence_$TIMESTAMP.tar.gz" -C backend/data/evidence .
```

### 5.3 Restore Database

```bash
gunzip < "$BACKUP_DIR/db_TIMESTAMP.sql.gz" | docker compose exec -T postgres psql -U iv_user -d industrialvision
```

---

## 6. RTSP & Camera Streaming in Docker Caveats

1. **Network Latency & UDP Packet Drops**:
   - RTSP streams via UDP can suffer packet loss in virtualized networks.
   - Prefer TCP transport where supported (`rtsp_transport=tcp`).

2. **Docker Host Network Mode for High-Bandwidth Multi-Camera**:
   - For factory deployments with 8+ concurrent 4K RTSP cameras, bridging introduces packet routing overhead.
   - Consider `network_mode: host` on Linux edge devices to eliminate Docker NAT latency.

3. **Credential Redaction**:
   - Never embed credentials in query parameters or commit them to version control.
   - All RTSP credentials in API output, logs, and database display are automatically sanitized with `[REDACTED]`.

---

## 7. Security Notes & Compliance

- **Non-Root Containers**: Both `backend` (appuser, UID 10001) and `frontend` (nginx, UID 101) drop root privileges.
- **Database Isolation**: The `postgres` container has **no public ports mapped**. It communicates strictly within the private `internal-net` network.
- **Path Traversal Protection**: Evidence retrieval and storage are strictly contained below configured root paths with directory traversal prevention (`sanitize_path`).
- **Fail-Safe Decision Rule**: Unhandled exceptions, missing models, or uncalibrated anomaly scores never result in a false `PASS`. Fail-safe defaults enforce `REVIEW` or `ERROR`.

---

## 8. Known Limitations & Workarounds

- **GPU Acceleration**: CUDA execution requires NVIDIA Container Toolkit installed on the host and runtime directives in `docker-compose.yml`. CPU fallback is enabled by default.
- **Real-Time Video FPS**: Processing high-resolution video frames at >30 FPS requires dedicated edge compute or frame sampling (`--frame-interval`).
