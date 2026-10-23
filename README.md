# IndustrialVision AI

IndustrialVision AI is a work-in-progress quality-inspection platform. It currently provides a FastAPI backend with authentication/RBAC, SQLite-backed inspection persistence, an image inspection endpoint, history/detail endpoints, model-neutral inference contracts, and safe evidence storage. **It is not a production-ready inspection system.**

## Verified in this workspace

- The runtime configuration can be loaded from environment variables and validated for development/production settings.
- The inspection decision service distinguishes `PASS`, `FAIL`, `REVIEW`, and `ERROR`; missing policy returns `REVIEW`, and inference failure returns `ERROR`.
- SQLite-backed tests verify authentication/RBAC, migrations, inspection persistence, upload validation, evidence storage, and mocked inference contracts. These tests do not validate an actual defect-detection model.
- The image inspection API and inspection history/detail routes are covered by tests; failed model loading is persisted as `ERROR`, never `PASS`.
- The FastAPI application imports and exposes OpenAPI at `/api/openapi.json`; current routes include `/auth/login`, `/health`, `/cameras`, `/admin/users`, `/inspections/image`, and `/inspections`.

## Not implemented or not validated

- No industrially trained model or dataset is present/validated. The ONNX adapter accepts only the explicit `xyxy_conf_class_nx6` output layout; it has not been validated against an actual model artifact.
- YOLO adapter behavior is unit-tested with mocks, but Ultralytics runtime and any real YOLO weights/classes have not been validated. Model paths are constrained under configured `MODEL_ROOT`.
- Video inspection, browser dashboard, camera hardware/RTSP capture, and live WebSocket inspection are not implemented/validated.
- PostgreSQL runtime/migrations, Docker deployment, and production security have not been validated.
- PatchCore/PaDiM inference, model training, and model evaluation are not implemented.
- There are no default credentials. Create the first administrator interactively with `python -m app.cli create-admin --username <username>` from `backend/`; do not expose bootstrap credentials in source/config.

## Architecture status

The intended modular-monolith flow is:

```text
Image / Video / Camera
  → frame acquisition and preprocessing
  → model adapter and postprocessing
  → decision rules
  → inspection/evidence persistence
  → REST/WebSocket API
  → operator dashboard
```

Currently verified implementation is limited to the decision/persistence core with mocked inference. The architecture diagram describes the target, not a complete running production system.

## Runtime configuration

The canonical settings module is `backend/app/core/config.py`; `backend/app/config.py` is a compatibility re-export. Copy `.env.example` to `.env` in the directory you run the application from (repository root for root-level commands, or `backend/` for commands run there). Database URLs must use synchronous SQLAlchemy drivers. SQLite is for local development; `APP_ENV=production` requires PostgreSQL, a non-placeholder `JWT_SECRET` of at least 32 characters, `DEBUG=false`, and explicit CORS origins.

Do not commit `.env` or real credentials. `.env.example` contains placeholders only.

## Development checks

From the `backend/` directory:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app migrations tests
python -c "from app.main import app; print(app.title, app.version)"
```

The first command currently runs 57 tests without a camera, GPU, or real model. `pytest` is not installed in the checked environment. Tests use isolated SQLite databases and mocked model inference; they do **not** prove that inference works on an industrial dataset. SQLite migration upgrade/downgrade is tested. PostgreSQL is not available in the checked environment.

## API status

OpenAPI is exposed at `/api/openapi.json`. Verified route paths include `POST /auth/login`, `GET /health`, camera routes under `/cameras`, admin users under `/admin/users`, `POST /inspections/image`, `GET /inspections`, and `GET /inspections/{inspection_id}`. Inspection history supports pagination and filters. Authentication is required for inspection endpoints. Treat the API as evolving; consult OpenAPI for request/response schemas.

## Project layout

```text
backend/
├── app/
│   ├── api/          # Auth, camera, admin, health, and inspection routers
│   ├── core/         # Canonical settings, security, JWT helpers
│   ├── db/           # SQLAlchemy models and session factory
│   ├── schemas/      # Pydantic API and inference schemas
│   ├── services/     # Inspection, inference contracts, and evidence
│   └── vision/       # Model adapters
├── migrations/       # Alembic migration environment and revisions
└── tests/            # unittest configuration, auth, API, migration, and service tests
.env.example          # Secret-free environment template
PLANNING.md           # Implementation backlog and acceptance criteria
```

## Repository hygiene

`.gitignore` excludes environment secrets, local databases, generated logs, Python caches, evidence, downloaded model weights, datasets, and frontend build/dependency output. Keep real datasets, evidence, and model artifacts outside Git unless their licensing and distribution terms have been reviewed.

## License and reference projects

The reference projects and their public README/license summaries are listed in `PLANNING.md`. Their source code, dependency lockfiles, and model/data artifacts have not been audited or copied. Confirm the license for this repository and every model/dataset/dependency before redistribution or commercial use.

## Next work

See `PLANNING.md` for the implementation backlog. Phase 1 database/migration, auth/RBAC, inference contracts, model adapters, image inspection, history/detail, and evidence-storage tasks have been implemented and locally tested. Next is the video/camera source abstraction; PostgreSQL, actual model inference, and deployment remain unvalidated.
