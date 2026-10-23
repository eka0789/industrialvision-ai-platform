# IndustrialVision AI

## Real-time defect detection & quality inspection system for industrial manufacturing

## Overview

IndustrialVision AI is a FastAPI-based platform for real-time industrial defect detection using ONNX runtime and computer vision. The system enables:

- **Real-time camera streaming** with WebSocket integration
- **ONNX model inference** for defect detection
- Inspection lifecycle persistence with fail-safe PASS/FAIL/REVIEW/ERROR decisions
- Optional anomaly-score rules that only consume scores returned by an anomaly model
- Evidence file storage for inspection records
- **Role-based access control** (admin/operator/viewer)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
├─────────────────────┬─────────────────────┬───────────────┤
│   API Routes        │   Services          │   DB/Models   │
│  (REST + WS)        │  (inference, camera │ (PostgreSQL)  │
│                     │   stream, inspection)│               │
└─────────────────────┴─────────────────────┴───────────────┘
           ↑                               ↑
        JWT Auth                        ONNX Runtime
```

## Features

### Core
- ✅ Real-time camera streaming via WebSocket
- ✅ ONNX object detection inference pipeline
- ✅ Inspection session lifecycle (start → process → end)
- ✅ Configurable decision policy: configured defect detections can FAIL; missing policies return REVIEW; inference errors return ERROR
- ⚠️ PatchCore/PaDiM inference is not implemented; detection confidence is never treated as an anomaly score
- ✅ JWT authentication with PBKDF2 password hashing
- ✅ Role-based access control (3 roles: admin, operator, viewer)

### API Endpoints
| Category | Endpoints |
|----------|-----------|
| Auth | `POST /auth/login`, `GET /auth/me` |
| Cameras | `GET/POST /cameras`, `GET/PUT/DELETE /cameras/{id}` |
| Inspections | `POST /inspections/start`, `POST /inspections/{id}/end` |
| Files | `POST /files/upload/{id}` |
| Admin | `POST /admin/seed` |

### Database
- 12+ core tables (users, roles, cameras, production_lines, model_versions, inspections, detections, anomaly_results, inspectionsessions, etc.)
- Alembic migrations managed
- Seed data script available

### Inspection decision policy

`ModelVersion.config_json` may define `reject_labels` (list of defect class names), `detection_confidence` (0–1), and optionally `anomaly_threshold` plus `review_band`. A successful frame with a configured rejection policy and no matching defects may PASS. If neither defect labels nor an anomaly threshold is configured, the result is REVIEW rather than an implicit PASS. An inference exception is persisted as ERROR. Anomaly thresholds require a real anomaly-model score; the current ONNX detector does not provide PatchCore/PaDiM scores.

The lifecycle/persistence service is covered by SQLite-backed unit tests, but an authenticated image/video REST inspection endpoint and a compatible production detection model are not yet implemented or validated.

## Runtime Configuration

The canonical settings are defined in `backend/app/core/config.py`; `backend/app/config.py` remains as a compatibility re-export. Copy `.env.example` to `backend/.env` and adjust values for the deployment. Database URLs use synchronous SQLAlchemy drivers: SQLite is intended for local development and PostgreSQL is required when `APP_ENV=production`. Production configuration rejects the development JWT secret, debug mode, wildcard CORS, and non-PostgreSQL database URLs. CORS origins are comma-separated.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up database
createdb industrialvision_ai  # or use Docker: docker compose up -d db
alembic upgrade head

# 3. Seed initial data
python -c "from app.main import app; from app.api.v1.routers.admin.admin import seed_initial_data"

# 4. Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Access

- **API Docs**: `http://localhost:8000/api/docs`
- **Health Check**: `GET /health/`
- **Login**: `POST /auth/login` (default: admin/admin123)

## Project Structure

```
backend/
├── app/                    # Main application code
│   ├── api/               # API routes (v1)
│   ├── core/              # Configuration, security, JWT
│   ├── db/                # Database models, session
│   ├── models/            # SQLAlchemy models
│   ├── schemas/           # Pydantic schemas
│   ├── services/          # Business logic services
│   │   ├── inference.py   # ONNX inference engine
│   │   ├── camera.py      # Camera streamer
│   │   └── inspection_manager.py  # Inspection session mgmt
│   └── main.py            # App factory
├── migrations/            # Alembic migrations
├── evidence/              # Uploaded evidence files
├── seed_data.py           # Seed data script
└── test_import.py         # Import verification
```

## Technologies

- **FastAPI** - API framework
- **SQLAlchemy** - ORM with PostgreSQL
- **Alembic** - Database migrations
- **ONNX Runtime** - Model inference
- **OpenCV** - Camera/image processing
- **Pydantic** - Data validation
- **JWT** - Authentication
- **PBKDF2** - Password hashing (240k iterations)

## Development

```bash
# Run tests (if any)
python -m pytest

# Lint
flake8 app/

# Type check (if configured)
```

## License

This project is licensed under the MIT License.

## Contact

For questions or support, please open an issue in the GitHub repository.