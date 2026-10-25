# IndustrialVision AI

IndustrialVision AI is a work-in-progress quality-inspection platform. It currently provides a FastAPI backend with authentication/RBAC, SQLite-backed inspection persistence, an image inspection endpoint, history/detail endpoints, model-neutral inference contracts, and safe evidence storage. **It is not a production-ready inspection system.**

## Verified in this workspace

- The runtime configuration can be loaded from environment variables and validated for development/production settings.
- The inspection decision service distinguishes `PASS`, `FAIL`, `REVIEW`, and `ERROR`; missing policy returns `REVIEW`, and inference failure returns `ERROR`.
- SQLite-backed tests verify authentication/RBAC, migrations, inspection persistence, upload validation, evidence storage, and mocked inference contracts. These tests do not validate an actual defect-detection model.
- The image inspection API and inspection history/detail routes are covered by tests; failed model loading is persisted as `ERROR`, never `PASS`.
- The FastAPI application imports and exposes OpenAPI at `/api/openapi.json`; current routes include `/auth/login`, `/health`, `/cameras`, `/admin/users`, `/inspections/image`, `/inspections`, and the `/models` registry routes.
- **Dataset tooling:** `python -m app.datasets.cli <root> --report report.json` validates image/label pairing, class IDs, boxes/masks, empty/corrupt files, performs a deterministic source-identity-grouped split, detects leakage, and writes a machine-readable JSON report (with optional SHA-256 checksums and a stable dataset fingerprint). 16 tests.
- **Model registry:** `/models` (list/create/patch/activate/deactivate) and `/models/{id}/evaluations` track framework, artifact reference + server-side checksum, task, classes, version, thresholds, training-data provenance, license, and active status. Writes require `admin`/`ai_engineer`; artifact input is a relative reference resolved strictly under `MODEL_ROOT` (absolute paths, traversal, missing files, unknown extensions → 422). 10 tests; migration `a00000000003` verified up/downgrade.
- **Training/evaluation:** `python -m app.training.train` records seed, config snapshot, environment, log, checkpoints, and dataset identity per run, writing `metrics.json` only on a completed run; `python -m app.training.evaluate` refuses non-completed runs and binds metrics to a hashed split file. Pure-Python detection metrics (precision/recall/F1/AP/mAP) are covered with known-value fixtures. 22 tests, including subprocess runs of both CLIs.

## Not implemented or not validated

- No industrially trained model or dataset is present/validated. The ONNX adapter accepts only the explicit `xyxy_conf_class_nx6` output layout; it has not been validated against an actual model artifact.
- No public dataset is redistributed or downloaded in this repository. `docs/DATASETS.md` documents verified sources, provider-stated licenses (including research-only/uncleared ones), download steps, layout, and checksum recording; test fixtures are synthetic and must not be labeled industrial data.
- No real training has run: Ultralytics is not installed in this workspace. The training entry point's missing-runtime path is verified — the run is marked `failed` with a reason and **no metrics file** is written.
- YOLO adapter behavior is unit-tested with mocks, but Ultralytics runtime and any real YOLO weights/classes have not been validated. Model paths are constrained under configured `MODEL_ROOT`.
- Video inspection, browser dashboard, camera hardware/RTSP capture, and live WebSocket inspection are not implemented/validated.
- PostgreSQL runtime/migrations, Docker deployment, and production security have not been validated.
- PatchCore/PaDiM inference is not implemented. Training/evaluation entry points exist (`app.training.train` / `app.training.evaluate`) with reproducibility gates, but neither has been exercised against a real dataset or an actual training runtime.
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

The first command currently runs 174 tests (2026-10-24) without a camera, GPU, or real model. `pytest` is not installed in the checked environment. Tests use isolated SQLite databases and mocked model inference; they do **not** prove that inference works on an industrial dataset. SQLite migration upgrade/downgrade is tested. PostgreSQL is not available in the checked environment.

## API status

OpenAPI is exposed at `/api/openapi.json`. Verified route paths include `POST /auth/login`, `GET /health`, camera routes under `/cameras`, admin users under `/admin/users`, `POST /inspections/image`, `GET /inspections`, `GET /inspections/{inspection_id}`, and the model registry routes `GET/POST /models/`, `GET/PATCH /models/{model_id}`, `POST /models/{model_id}/activate|deactivate`, `GET/POST /models/{model_id}/evaluations`. Inspection history supports pagination and filters. Authentication is required for inspection and model endpoints; model writes additionally require `admin` or `ai_engineer`. `POST /cameras/{camera_id}/inspect-video` includes a `metrics` snapshot (units, measurement windows, hardware metadata). Treat the API as evolving; consult OpenAPI for request/response schemas.

## Project layout

```text
backend/
├── app/
│   ├── api/          # Auth, camera, admin, health, inspection, and model routers
│   ├── core/         # Canonical settings, security, JWT helpers
│   ├── datasets/     # Dataset validation, split, and leakage tooling (T18)
│   ├── db/           # SQLAlchemy models and session factory
│   ├── schemas/      # Pydantic API and inference schemas
│   ├── services/     # Inspection, inference, evidence, model registry, runtime metrics
│   ├── training/     # Reproducible run manifests, train/evaluate entry points (T21)
│   └── vision/       # Model adapters
├── migrations/       # Alembic migration environment and revisions
└── tests/            # unittest configuration, auth, API, migration, and service tests
.env.example          # Secret-free environment template
docs/DATASETS.md      # Dataset sources, licenses, layout, checksums (T19)
PLANNING.md           # Implementation backlog and acceptance criteria
```

## Repository hygiene

`.gitignore` excludes environment secrets, local databases, generated logs, Python caches, evidence, downloaded model weights, datasets, and frontend build/dependency output. Keep real datasets, evidence, and model artifacts outside Git unless their licensing and distribution terms have been reviewed.

## License and reference projects

The reference projects and their public README/license summaries are listed in `PLANNING.md`. Their source code, dependency lockfiles, and model/data artifacts have not been audited or copied. Confirm the license for this repository and every model/dataset/dependency before redistribution or commercial use.

## Next work

See `PLANNING.md` for the implementation backlog. Phases 1–5 are implemented and locally tested, including dataset validation tooling, the dataset setup guide, the model registry API, reproducible training/evaluation entry points, and the runtime metrics collector (latency/FPS windows, CPU-RAM-GPU sampling, frame drops, reconnects, inference errors with explicit units and hardware metadata — 174 tests). Next is T23 (benchmark script/report template); PostgreSQL, real model inference/training, and deployment remain unvalidated.
