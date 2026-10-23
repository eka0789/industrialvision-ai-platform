# IndustrialVision AI

IndustrialVision AI is a work-in-progress quality-inspection platform. The repository currently contains a partial FastAPI/SQLAlchemy backend and a fail-safe inspection decision service. **It is not a production-ready inspection system.**

## Verified in this workspace

- The runtime configuration can be loaded from environment variables and validated for development/production settings.
- The inspection decision service distinguishes `PASS`, `FAIL`, `REVIEW`, and `ERROR`; missing policy returns `REVIEW`, and inference failure returns `ERROR`.
- SQLite-backed tests verify persistence using mocked inference results. These tests do not validate an actual defect-detection model.
- The FastAPI application imports and exposes OpenAPI at `/api/openapi.json`.

## Not implemented or not validated

- No industrially trained model or dataset is present/validated. The ONNX postprocessor is model-dependent and is not validated against a supported output contract.
- There is no complete image/video inspection REST API, browser dashboard, or end-to-end live WebSocket inspection flow.
- Camera/video capture code has not been validated against hardware or sample media.
- PostgreSQL connectivity/migrations, Docker deployment, and production security have not been validated.
- PatchCore/PaDiM inference, model training, and model evaluation are not implemented.
- The current API routers are included with duplicate prefixes. For example, OpenAPI lists `/auth/auth/login`, `/health/health`, and `/cameras/cameras`. Treat these as current implementation paths, not as a stable API contract. The evidence upload route also has known unresolved implementation issues.
- The admin seed router is not currently included in the application. There is no verified default username/password; do not rely on README examples or expose this service publicly.

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

The first command currently runs 25 tests without a camera, GPU, or real model. `pytest` is not installed in the checked environment. These checks cover configuration, decision rules, and mocked SQLite persistence; they do **not** prove that inference works on an industrial dataset.

A full server startup, database migration against a fresh database, and seeded login flow have not yet been verified. Resolve the duplicate route prefixes, migration/config wiring, and admin/bootstrap flow before documenting a `curl`-based operational quick start.

## API status

OpenAPI is exposed at `/api/openapi.json` when the app imports. The paths shown there currently include duplicated router prefixes and are subject to change. Do not integrate external systems against them until the API routing task is completed and contract tests are in place.

## Project layout

```text
backend/
├── app/
│   ├── api/          # Partial auth, camera, health, and evidence routers
│   ├── core/         # Canonical settings, security, JWT helpers
│   ├── db/           # SQLAlchemy models and session factory
│   ├── schemas/      # Pydantic schemas
│   └── services/     # Camera, ONNX, and inspection/decision services
├── migrations/       # Alembic migration environment and revisions
└── tests/            # unittest configuration, decision, persistence tests
.env.example          # Secret-free environment template
PLANNING.md           # Implementation backlog and acceptance criteria
```

## Repository hygiene

`.gitignore` excludes environment secrets, local databases, generated logs, Python caches, evidence, downloaded model weights, datasets, and frontend build/dependency output. Keep real datasets, evidence, and model artifacts outside Git unless their licensing and distribution terms have been reviewed.

## License and reference projects

The reference projects and their public README/license summaries are listed in `PLANNING.md`. Their source code, dependency lockfiles, and model/data artifacts have not been audited or copied. Confirm the license for this repository and every model/dataset/dependency before redistribution or commercial use.

## Next work

See `PLANNING.md`. The next engineering priority after runtime configuration is to make database/migrations reproducible, then validate auth/RBAC and define a model output contract before implementing image inference and APIs.
