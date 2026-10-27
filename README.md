# IndustrialVision AI

<p align="left">
  <img src="https://img.shields.io/badge/Status-Active%20Development-1c211b?style=for-the-badge&logoColor=d6f779" alt="Status" />
  <img src="https://img.shields.io/badge/Frontend-React%2019%20%7C%20TypeScript-1c211b?style=for-the-badge&logo=react&logoColor=61dafb" alt="Frontend" />
  <img src="https://img.shields.io/badge/Backend-FastAPI%20%7C%20Python%203.13-1c211b?style=for-the-badge&logo=fastapi&logoColor=009688" alt="Backend" />
  <img src="https://img.shields.io/badge/Database-SQLite%20%2F%20PostgreSQL%2016-1c211b?style=for-the-badge&logo=postgresql&logoColor=4169e1" alt="Database" />
  <img src="https://img.shields.io/badge/Accessibility-WCAG%202.1%20AA%20Compliant-1c211b?style=for-the-badge&logoColor=d6f779" alt="WCAG AA" />
  <img src="https://img.shields.io/badge/Tests-223%20Passing-1c211b?style=for-the-badge&logoColor=d6f779" alt="Tests" />
</p>

**IndustrialVision AI** is a computer-vision-based visual inspection platform for manufacturing and quality control environments. The system integrates image testing, AI model management, operator workflows, and evidence logging to support decision-making on the production floor.

> **Production Readiness Note:** This repository provides a modular software foundation that has passed local integration tests and API contract validation. Real deployment on a live production floor still requires environment-specific validation, governance, and operational monitoring.

---

## Table of Contents

1. [Core Features](#core-features)
2. [System Architecture](#system-architecture)
3. [Technology Stack](#technology-stack)
4. [System Requirements](#system-requirements)
5. [Application Startup Guide (Local Development)](#application-startup-guide-local-development)
   - [1. Clone & Install Dependencies](#1-clone--install-dependencies)
   - [2. Environment Configuration](#2-environment-configuration)
   - [3. Database Migration & Admin Bootstrap](#3-database-migration--admin-bootstrap)
   - [4. Running the Backend API](#4-running-the-backend-api)
   - [5. Running the Frontend Console](#5-running-the-frontend-console)
6. [Operational Workflow](#operational-workflow)
7. [Project Structure](#project-structure)
8. [REST & WebSocket API Reference](#rest--websocket-api-reference)
9. [Environment Variables](#environment-variables)
10. [Testing & Quality Verification](#testing--quality-verification)
11. [Docker Compose Deployment](#docker-compose-deployment)
12. [Troubleshooting](#troubleshooting)
13. [Security & System Limitations](#security--system-limitations)
14. [License & Contribution](#license--contribution)

---

## Core Features

- **Industrial Operator Console (Anti-AI Slop UI):**
  A strong, industrial interface using a graphite palette (`#1c211b`), warm off-white (`#f3f4ed`), and signal lime (`#d6f779`). Includes locally bundled typography (*Space Grotesk*, *Manrope*, *IBM Plex Mono*) and a clear operational layout designed for production environments.
- **Image Inspection Workflow & Visual Evidence:**
  Upload sample product images (JPEG, PNG, WebP up to 100 MB). Quality decisions (`PASS`, `FAIL`, `REVIEW`, `ERROR`) are generated alongside numerical inference latency, decision rationale, and detection overlays.
- **Secure Evidence Storage:**
  Evidence images are stored with server-side path validation to prevent path traversal attacks. Retrieval of visual evidence via `GET /inspections/{id}/evidence` requires a valid JWT token and is isolated from direct public access.
- **AI Model Registry & Management:**
  Version tracking for AI models (ONNX, YOLO), task type registration (object detection, image anomaly, anomaly segmentation), defect classes, confidence thresholds, automatic SHA-256 checksum validation of server artefacts, and activation/deactivation controls.
- **Inspection Station Management (Fleet):**
  Registration of workstations with flexible sources (*browser upload*, RTSP, USB webcam, local file, simulation) and automatic redaction of RTSP credentials in stored URLs.
- **Role-Based Access Control (RBAC):**
  Strict separation of permissions at both the API and web interface layers:
  - `admin`: full configuration, station setup, user account management.
  - `ai_engineer`: full operational access, model registration, activation/evaluation.
  - `operator`: daily inspection execution, evidence review, operational status changes for stations.
- **Accessibility & Mobile Standards:**
  Passes automated **WCAG 2.1 AA** compliance checks (text contrast, keyboard focus, native modal focus containment) and adaptive mobile layout on 390 px viewports without horizontal overflow.

---

## System Architecture

The platform follows a **Modular Monolith** pattern with clear separation between domain logic, hardware/model adapters, and presentation layers:

```text
[ Camera / File / Browser Upload ]
                 │
                 ▼
[ Fastify / Starlette Proxy (Vite Dev / Nginx Production) ]
                 │
                 ▼
[ FastAPI Application Core (Python 3.13) ]
  ├── Auth & RBAC (PyJWT, Password Hashing)
  ├── Security Hardening (Upload Bounds, Path Sanitization, Secret Redaction)
  ├── Camera Service & Capture Worker (Bounding Deque, RTSP Redaction)
  ├── Inference & Anomaly Engine (ONNX Runtime, NumPy Adapters)
  ├── Decision Engine (Rules-based Evaluation: PASS / FAIL / REVIEW / ERROR)
  └── Evidence Storage (Server-rooted, Collision-safe UUIDs)
                 │
        ┌────────┴────────┐
        ▼                 ▼
[ SQLite / PostgreSQL ] [ File Storage (data/evidence) ]
        ▲
        │
[ React 19 Frontend Console (SPA + TailwindCSS Tokens) ]
  ├── AuthProvider & Route Guards (useRequireRole)
  ├── Operations Overview & Quality Index Gauge
  ├── Inspection Workbench & Precision SVG Detection Canvas
  ├── Filterable Traceability History & JSON Data Export
  └── Stations Fleet & Model Registry Consoles
```

---

## Technology Stack

### Backend & AI Runtime
- **Language & Framework:** Python 3.13+, FastAPI, Starlette, Uvicorn
- **ORM & Migrations:** SQLAlchemy 2.0 (Synchronous Engine), Alembic
- **Computer Vision & Inference:** OpenCV Headless, Pillow, NumPy, ONNX Runtime, ONNX
- **Security & Authentication:** PyJWT, Passlib (Argon2 / BCrypt)

### Frontend Console
- **Framework:** React 19, TypeScript 5.8+, Vite 8
- **Routing & State:** React Router 7, Context API, custom hooks (`useAuth`, `useApi`, `useToast`)
- **Design & Icons:** Custom CSS tokens, Tailwind CSS v4, Lucide React
- **Typography:** `@fontsource-variable/space-grotesk`, `@fontsource-variable/manrope`, `@fontsource/ibm-plex-mono`
- **Automated Testing:** Playwright, `@axe-core/playwright`

### Infrastructure & Deployment
- **Containerization:** Docker multi-stage builds, Docker Compose
- **Production Web Server:** Nginx unprivileged (`nginxinc/nginx-unprivileged:alpine-slim`)
- **Production Database:** PostgreSQL 16 Alpine

---

## System Requirements

Before starting, ensure your environment includes:

- **Python:** Version 3.11 to 3.13 (recommended 3.13)
- **Node.js:** Version 20.x or 22.x LTS (with `npm` 10+)
- **Git:** Latest version
- **Docker & Docker Compose:** *(Optional, if you want container-based deployment)*
- **OS:** Windows 10/11, macOS, or Linux (Ubuntu 22.04+)

---

## Application Startup Guide (Local Development)

### 1. Clone & Install Dependencies

```bash
# Clone the repository
git clone https://github.com/user/industrialvision-ai-platform.git
cd industrialvision-ai-platform

# Install Python backend dependencies
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt

# Install React frontend dependencies
npm --prefix frontend install
```

### 2. Environment Configuration

Copy the sample configuration files to the project root and frontend directory:

```bash
# Main backend environment
cp .env.example .env

# Frontend environment
cp frontend/.env.example frontend/.env
```

For local testing, the default values in `.env.example` (SQLite at `./industrialvision_ai.db`) are ready to use immediately.

### 3. Database Migration & Admin Bootstrap

Run the database schema migration and create your first administrator account:

```bash
cd backend

# Apply Alembic database migrations
alembic upgrade head

# Create the first admin (this only applies if the database has no admin yet)
python -m app.cli create-admin --username admin_factory
```

The system will prompt for a password interactively (minimum 12 characters required).

> **Important Note About Admin Accounts:**  
> The command `python -m app.cli create-admin` can only be run once during initial installation. If the database already contains an admin, additional user creation (operators, engineers, or admin accounts) must be performed through the web UI or admin user-management API.

#### Populate Demo Data (Optional, Recommended for Demos/Development)

To fill the database with a complete, realistic dummy dataset — demo accounts, production lines, inspection stations, the AI model registry with evaluation history, hundreds of inspection records with synthetic evidence images, system events, and audit logs — run:

```bash
python -m app.seed --reset
```

This also activates the **live feed**: a backend background task generates a new inspection every few seconds for simulated stations, so the dashboard ticks in real time (marked by the **LIVE** indicator in the web console). Additional flags:

```bash
python -m app.seed --append                # add inspections without wiping data
python -m app.seed --reset --inspections 500 --hours 168   # a larger dataset (7 days)
python -m app.seed --reset --no-evidence   # skip evidence images (faster)
```

**Demo accounts created by the seeder:**

| Username | Password | Role |
| --- | --- | --- |
| `admin` | `Admin!Vision2026` | admin |
| `operator1` | `Operator!2026` | operator |
| `aiengineer` | `Engineer!2026` | ai_engineer |

> ⚠️ These demo credentials are for development environments only. Disable the live feed with `DEMO_LIVE_FEED=false` and replace all demo accounts before using the system anywhere else.

### 4. Running the Backend API

From the `backend` folder:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The backend is available at:
- **API Base:** [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Interactive Swagger Documentation:** [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs)
- **OpenAPI JSON:** [http://127.0.0.1:8000/api/openapi.json](http://127.0.0.1:8000/api/openapi.json)

### 5. Running the Frontend Console

Open a new terminal in the project root:

```bash
cd frontend
npm run dev
```

Open your browser at [http://127.0.0.1:5173](http://127.0.0.1:5173).

> **Vite Proxy Flow:**  
> The frontend is configured with a `/api/v1` proxy edge that forwards requests to the backend on port `8000`. This prevents conflicts with the SPA routing layer and keeps frontend routes like `/camera` and `/models` isolated from backend APIs.

---

## Operational Workflow

After logging in, follow these steps to start inspections:

1. **Log In to the Control Room:**  
   Open `/login` and sign in using your administrator credentials.
2. **Register a Workstation:**  
   Open the **Stations & Cameras** menu (`/cameras`), click **Add station**, assign a name (for example `Line 01 - Visual Stamping`), and choose the source type **Upload image**.
3. **Register Model Artefacts:**  
   Open the **Model registry** page (`/models`). Ensure the ONNX model file is located in the `backend/models/` folder. Enter the filename (for example `detector.onnx`), define the defect classes, output format configuration, and activation settings.
4. **Run a Sample Inspection:**  
   Open the **New inspection** page (`/inspections/new`), select the station and active model, then upload a product component image. The system performs inference, stores the original evidence, and displays the bounding boxes and decisions.
5. **Audit the Quality Trace:**  
   Open the **Inspection history** page (`/inspections`) to view decision logs, filter by outcome or batch, and export the audit trail as JSON.

---

## Project Structure

```text
industrialvision-ai-platform/
├── .github/workflows/          # CI/CD GitHub Actions (lint, test, build, compose audit)
├── backend/                    # Core API, domain services, and computer vision engine
│   ├── app/
│   │   ├── api/v1/             # REST API routers (auth, cameras, inspections, models, admin)
│   │   ├── core/               # Pydantic configuration, security, logging sanitization, constants
│   │   ├── db/                 # SQLAlchemy models, session factory, base metadata
│   │   ├── schemas/            # Pydantic request/response validation schemas
│   │   ├── services/           # Business logic: decision engine, evidence storage, camera worker
│   │   └── vision/             # Inference adapters (ONNX Runtime, YOLO, PatchCore/PaDiM)
│   ├── migrations/             # Alembic migration environment
│   ├── tests/                  # 223 unit and integration tests using unittest
│   ├── alembic.ini             # Database migration configuration
│   ├── Dockerfile              # Multi-stage backend container build (non-root appuser)
│   └── requirements.txt        # Unified Python dependencies
├── frontend/                   # Operator console web app
│   ├── public/                 # Favicon and static assets
│   ├── src/
│   │   ├── api/                # HTTP client with JWT bearer interceptor and auto-logout
│   │   ├── components/         # UI components: Brand, EvidenceImage, InspectionTable, Modal, etc.
│   │   ├── hooks/              # Custom hooks: useAuth, useApi, useToast
│   │   ├── layouts/            # AppShell with industrial navigation and mobile drawer
│   │   ├── pages/              # Pages: Overview, NewInspection, History, Cameras, Models, Admin
│   │   ├── styles/             # Design system modules: app.css, pages.css, responsive.css
│   │   ├── types/              # Unified TypeScript declarations
│   │   ├── utils/              # Data formatters and multilingual error parsers
│   │   ├── App.tsx             # Routing and role-based route guards
│   │   ├── index.css           # Color tokens, typography, and CSS variables
│   │   └── main.tsx            # React 19 entry point
│   ├── nginx.conf              # Reverse proxy config, SPA fallback, and security headers
│   ├── Dockerfile              # Multi-stage Nginx build
│   └── package.json            # React, TypeScript, and Vite dependencies
├── docs/                       # Architecture, dataset, operations, and release gate documents
├── docker-compose.yml          # Multi-container orchestration (PostgreSQL, Backend, Frontend)
├── .env.example                # Environment variable template
└── README.md                   # Project technical documentation
```

---

## REST & WebSocket API Reference

All business endpoints require the `Authorization: Bearer <token_jwt>` header unless they are public endpoints.

| Module | Method | Endpoint | Role Access | Description |
|---|---|---|---|---|
| **System** | `GET` | `/health` | Public | Server status, version, database connection, and active stations. |
| **Auth** | `POST` | `/auth/login` | Public | Username/password authentication; returns a JWT token. |
| **Auth** | `GET` | `/auth/me` | All Roles | Current logged-in user profile and access permissions. |
| **Auth** | `POST` | `/auth/logout` | Public | Client-side session invalidation (stateless). |
| **Cameras** | `GET` | `/cameras` | All Roles | List of all inspection workstations. |
| **Cameras** | `POST` | `/cameras` | `admin` | Register a new inspection workstation. |
| **Cameras** | `GET` | `/cameras/{id}` | All Roles | Detailed workstation information and specifications. |
| **Cameras** | `PUT` | `/cameras/{id}` | `admin` | Update name, resolution, FPS, or station status. |
| **Cameras** | `DELETE` | `/cameras/{id}` | `admin` | Remove a workstation from the system. |
| **Cameras** | `POST` | `/cameras/{id}/start` | `admin`, `operator` | Set workstation status to `connected`. |
| **Cameras** | `POST` | `/cameras/{id}/stop` | `admin`, `operator` | Set workstation status to `disconnected`. |
| **Inspections** | `POST` | `/inspections/image` | All Roles | Upload a sample image multipart and run inference. |
| **Inspections** | `GET` | `/inspections` | All Roles | Paginated inspection history with decision and batch filters. |
| **Inspections** | `GET` | `/inspections/{id}` | All Roles | Detailed result, latency, decision rationale, and defect findings. |
| **Inspections** | `GET` | `/inspections/{id}/evidence` | All Roles | Retrieve original evidence image securely (blob). |
| **Models** | `GET` | `/models/` | All Roles | Catalog of registered AI models filtered by task type. |
| **Models** | `POST` | `/models/` | `admin`, `ai_engineer` | Register model artefact metadata on the server. |
| **Models** | `POST` | `/models/{id}/activate` | `admin`, `ai_engineer` | Activate a model for active inference. |
| **Models** | `POST` | `/models/{id}/deactivate` | `admin`, `ai_engineer` | Deactivate a model from the inference queue. |
| **Models** | `GET` | `/models/{id}/evaluations` | All Roles | Evaluation performance history of the model. |
| **Admin** | `POST` | `/admin/users` | `admin` | Create a new operator or engineer account. |
| **Stream** | `WS` | `/ws/cameras/{id}/stream` | Valid JWT | Stream realtime camera metadata events. |
| **Stream** | `WS` | `/ws/inspections/live` | Valid JWT | Live feed of all inspection decisions (event bus). |

#### `WS /ws/inspections/live` Message Format

Authenticate via query token: `ws://host/ws/inspections/live?token=<jwt>`. Messages sent by the server:

```jsonc
// When the connection is accepted
{"type": "connected", "timestamp": 1769515200.1}
// Every time an inspection decision is stored (real pipeline or demo live feed)
{"type": "inspection.created", "inspection": {"id": 243, "decision": "PASS", "camera_id": 4, "detections": [], "anomaly": {"score": 0.21}, "latency_ms": 87.3, "timestamp": "2026-10-27T00:41:31.136064", "...": "..."}}
// Periodic heartbeat while there are no events
{"type": "heartbeat", "timestamp": 1769515215.1}
```

The web console subscribes to this feed through `LiveFeedProvider`: the Overview, History, and Stations pages update their data live without a reload, marked by the **LIVE** indicator.

---

## Environment Variables

| Variable | Description | Default / Example | Required in Production? |
|---|---|---|:---:|
| `APP_ENV` | Application environment mode (`development` / `production`). | `development` | Yes |
| `DEBUG` | Enables debug logging and SQL query echoing. | `false` | Yes (must be `false`) |
| `HOST` | Uvicorn bind host address. | `127.0.0.1` | Yes |
| `PORT` | Backend server port. | `8000` | Yes |
| `DATABASE_URL` | SQLAlchemy synchronous database URL. | `sqlite:///./industrialvision_ai.db` | Yes (must be PostgreSQL in production) |
| `JWT_SECRET` | Secret key for JWT hashing (minimum 32 characters). | `dev-only-insecure-secret...` | Yes (must be random and unique) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT session lifetime in minutes. | `15` | Yes |
| `CORS_ORIGINS` | Comma-separated list of allowed browser origins. | `http://localhost:5173,http://localhost:3000` | Yes (no wildcard `*`) |
| `MAX_UPLOAD_BYTES` | Max allowed image file size in bytes. | `104857600` (100 MB) | Optional |
| `EVIDENCE_ROOT` | Physical storage directory for visual evidence. | `data/evidence` | Optional |
| `MODEL_ROOT` | Storage directory for model artefacts. | `models` | Optional |
| `DEMO_LIVE_FEED` | Enables realtime simulated inspections for `simulated` stations (automatically disabled when `APP_ENV=test`). | `true` | Disable in production |
| `DEMO_FEED_INTERVAL_SECONDS` | Delay between inspections generated by the live feed. | `6` | Optional |

Frontend variables (`frontend/.env` file):

| Variable | Description | Default |
|---|---|---|
| `VITE_API_BASE_URL` | REST API base URL. | `/api/v1` (via Vite proxy) |
| `VITE_WS_URL` | Base URL of the live WebSocket feed. | Browser protocol & host (`ws(s)://location.host`) |

---

## Testing & Quality Verification

This project uses strict validation before code can be merged or released:

```bash
# 1. Run the 223 backend unit and integration tests
cd backend
python -m unittest discover -s tests -v

# 2. Verify Python code compilation
python -m compileall -q app migrations tests

# 3. Run the frontend linter (Oxlint)
npm --prefix frontend run lint

# 4. Type-check TypeScript and build the production frontend
npm --prefix frontend run build
```

Latest verification results:
- **Backend:** 223 tests passing (0 failed, 0 errors).
- **Frontend build:** Production bundle compiles cleanly without type errors.
- **Accessibility:** 0 WCAG 2.1 AA violations across the main routes.
- **Responsiveness:** 0 horizontal overflow on 390 px mobile viewport tests.

---

## Docker Compose Deployment

To run an isolated production-ready environment with PostgreSQL 16, a standalone backend, and an unprivileged Nginx frontend:

```bash
# 1. Ensure JWT_SECRET and POSTGRES_PASSWORD are set in .env
export JWT_SECRET="production-secret-key-minimum-thirty-two-characters"
export POSTGRES_PASSWORD="StrongPostgresPassword2026!"

# 2. Build and run the full stack
docker compose up -d --build

# 3. Check container status
docker compose ps
```

Services will be available at:
- **Frontend Console:** [http://localhost:8080](http://localhost:8080)
- **Backend Internal:** `http://backend:8000` (accessible internally via the frontend proxy)
- **Database:** PostgreSQL is isolated in the internal Docker network and does not expose a public host port.

---

## Troubleshooting

### 1. `An administrator already exists; use the admin user-management API`
The command `python -m app.cli create-admin` is only valid when creating the first administrator while the database is still empty. If your local database already contains an admin, log in through the web interface and use the user management flow instead.

### 2. API Status Shows "System requires attention" / Database Degraded
Make sure the SQLite database is accessible or that the PostgreSQL service is running. Health checks can be validated directly via:

```bash
curl http://127.0.0.1:8000/health
```

A normal response looks like: `{"status": "ok", "database": "ok", ...}`.

### 3. Inspection Evidence Image Does Not Display in the Browser
Ensure the `data/evidence/` directory has read/write permissions for the user running the backend process. The visual evidence endpoint requires a valid bearer token.

### 4. Bounding Boxes Are Misaligned
SVG bounding boxes are calculated using the image's natural dimensions and then aligned proportionally to the rendered image container. Ensure the image is not being distorted or resized with an incorrect aspect ratio during display.

---

## Security & System Limitations

- **Credential Sanitization:** RTSP camera URLs are automatically sanitized to remove username/password before storage or display in logs and APIs.
- **Path Traversal Prevention:** Requests for model files and evidence files are strictly validated against their designated base directories (`MODEL_ROOT` and `EVIDENCE_ROOT`). The use of `..` or malicious path sequences is rejected.
- **Live Stream Limitations:** Direct camera video streaming through the browser is not yet implemented as a complete end-to-end WebRTC/RTSP decode pipeline; validated production workflows rely on image upload and other supported inspection patterns.
- **Model Warning:** Activating a model in the interface only indicates that the artefact is available for inference; it does not guarantee the model has been fully validated for production defect detection accuracy.

---

## License & Contribution

This project is licensed under the terms of an internal / proprietary license. All code contributions must comply with quality review standards, pass the full unit-test suite, and maintain traceability for production safety and operational reliability.

---
