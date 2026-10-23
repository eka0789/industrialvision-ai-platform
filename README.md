# IndustrialVision AI

## Real-time defect detection & quality inspection system for industrial manufacturing

## Overview

IndustrialVision AI is a FastAPI-based platform for real-time industrial defect detection using ONNX runtime and computer vision. The system enables:

- **Real-time camera streaming** with WebSocket integration
- **ONNX model inference** for defect detection
- **Inspection session management** with PASS/FAIL/ERROR decisions
- **Anomaly scoring** based on detection confidence
- **Evidence file storage** for inspection records
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
- ✅ Anomaly scoring and PASS/FAIL decisions
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