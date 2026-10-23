# IndustrialVision AI — Implementation Backlog

**Status:** In progress — MVP is not production-ready
**Workspace checked:** 2026-10-23
**Runtime checked:** Python 3.14.2, Node 24.13.0. Docker and `pytest` are unavailable in the current environment.
**GitHub:** `https://github.com/eka0789/industrialvision-ai-platform`

This backlog breaks the master prompt into small, testable tasks. Each item should be implemented in a focused change, validated, documented, and only then marked done. “Done” means verified in this workspace, not inferred from code or a demo.

## Current verified baseline

- The project has a partial FastAPI/SQLAlchemy backend, Alembic files, basic auth and camera API code, ONNX inference code, and a SQLite database. Several paths/configurations are inconsistent and not all routers/features are wired into the app.
- `backend/app/services/decision.py` implements fail-safe PASS/FAIL/REVIEW/ERROR decisions. Decisions without a configured defect/anomaly policy return REVIEW.
- `backend/app/services/inspection_manager.py` now persists inference results and decision reasons; errors are recorded as ERROR. A successful PASS requires a configured rejection or anomaly policy.
- `python -m unittest discover -s tests -v` from `backend/` passed 57 tests, including configuration, auth/RBAC, SQLite migration upgrade/downgrade, inference contract/adapter mocks, image API, history/detail, evidence storage, decision, and persistence tests. `python -m compileall -q app migrations tests` passed.
- The image API and OpenAPI routes are wired; image inference persists actual adapter results, while unavailable model configuration is recorded as ERROR. YOLO/ONNX adapter behavior is unit-tested, but no actual industrial model/dataset or Ultralytics runtime has been validated. PostgreSQL runtime, video endpoint, frontend, and Docker have not been verified.
- The four reference repositories’ public pages and READMEs were reviewed; their source code, dependency lockfiles, and model/data artifacts were not audited. No reference code has been copied.

## Task board

### Phase 0 — Establish a trustworthy baseline

- [x] **T00.1 — Inspect workspace and record constraints.** Inventory current modules, installed runtimes, test availability, Docker availability, and existing code before changing it.
- [x] **T00.2 — Review reference README/license summaries.** Record what was actually checked and distinguish reported third-party metrics from this project’s results.
- [x] **T00.3 — Reconcile project documentation with actual behavior.** README distinguishes verified tests from unvalidated model/runtime/deployment, lists current routes and bootstrap instructions, removes stale duplicate-prefix/default-credential claims, and documents tested commands and blockers.
- [x] **T00.4 — Establish repository hygiene.** Added `.gitignore` entries for databases, evidence, model weights, caches, `.env`, generated logs, datasets, and frontend outputs. Existing untracked files were not deleted or staged.

### Phase 1 — Backend foundation and inspection core

- [x] **T01 — Consolidate runtime configuration.** `app.core.config` is the canonical settings module; legacy `app.config` re-exports it. Environment names/defaults are centralized, unsafe production defaults are rejected, and SQLite/PostgreSQL URL + CORS parsing are tested.
- [x] **T02 — Make database and migrations reproducible.** Aligned SQLAlchemy models/Alembic metadata and added a follow-up schema revision. Clean SQLite upgrade/downgrade passes in an isolated test database. PostgreSQL runtime remains unverified.
- [x] **T03 — Verify authentication and RBAC.** Tests cover password hashing, malformed/expired tokens, inactive users, role checks, and least-privilege routes. First-admin creation is an interactive CLI; no default credentials are provisioned.
- [x] **T04 — Decision engine and persistence baseline.** Support configured detection/anomaly rules, readable reasons, fail-safe inference errors, and persistence of actual mocked/real inference output. Unit and SQLite integration tests cover PASS, FAIL, REVIEW, and ERROR.
- [x] **T05 — Define inference contracts and validate output schemas.** Added model-neutral result schemas and validated detection/anomaly contracts. ONNX accepts only explicit `xyxy_conf_class_nx6`; unsupported layouts, invalid values, and unknown classes are rejected. Contract/model-load failure tests pass.
- [x] **T06 — Add an industrial object-detection adapter.** Added explicit local model path/config, class names, thresholds, image size, device, metadata, and YOLO adapter contract tests. Ultralytics runtime and real task-specific weights/classes are NOT validated; this does not establish industrial detection capability.
- [x] **T07 — Add image inspection API.** Authenticated multipart endpoint validates image type/signature, upload size and pixel count, decodes input, runs the configured adapter, and persists detections, decision, latency, and evidence. Model load/inference failures are ERROR, never PASS. API tests pass with mocked inference.
- [x] **T08 — Add inspection history/detail APIs.** Added authenticated, database-backed pagination/filtering and stable detail response; tests cover results and filters.
- [x] **T09 — Add safe evidence storage.** Evidence uses generated inspection IDs and configured root, validates identifiers/extensions, prevents traversal, and supports safe idempotent deletion; tests cover storage and cleanup.

### Phase 2 — Video and cameras

- [ ] **T10 — Implement camera source abstraction.** Support simulated/video-file source first, then webcam and RTSP. Clearly distinguish source types and redact RTSP credentials from API output/logs/errors.
- [ ] **T11 — Implement bounded capture worker.** Use bounded/latest-frame buffering, read timeout, no-frame detection, exponential-backoff reconnect, connection limits, metrics, and graceful shutdown. Cover disconnect/reconnect with mocked sources.
- [ ] **T12 — Add video-file inspection.** Validate codec/container and upload limits; process frames at configurable intervals; aggregate per-frame results without losing timestamps; persist evidence and report dropped frames/FPS/latency.
- [ ] **T13 — Add camera administration and session APIs.** Protect camera create/update/delete/start/stop with RBAC; persist status, last error, resolution, FPS, and last inspection. Never return stored camera secrets.

### Phase 3 — Real-time events and anomaly detection

- [ ] **T14 — Add WebSocket inspection events.** Authenticate/authorize sockets, send metadata and detection results rather than every full-resolution frame, handle disconnects, and test reconnect/fallback behavior.
- [ ] **T15 — Add anomaly detector interface.** Keep anomaly detection distinct from object detection. Define score/map contracts and threshold metadata; uncalibrated scores must not be called probabilities.
- [ ] **T16 — Add PatchCore/PaDiM adapter only after compatibility checks.** Isolate optional dependencies, document supported Python/Torch versions, train memory bank/distribution from normal samples, and gate unsupported runtime environments cleanly.
- [ ] **T17 — Add anomaly evaluation.** Implement image/pixel AUROC and localization metrics only with compatible ground truth; test metric functions on known/synthetic fixtures and document dataset license restrictions.

### Phase 4 — Dataset and model lifecycle

- [ ] **T18 — Dataset validation and split tooling.** Validate image/label pairing, class IDs, bounding boxes/masks, empty/corrupt files, deterministic split, and leakage by source identity. Produce a machine-readable report.
- [ ] **T19 — Dataset setup guide.** Document verified public sources, license/use restrictions, download steps, expected layout, and checksums where available. Do not redistribute restricted datasets or label synthetic assets as industrial data.
- [ ] **T20 — Model registry and metadata API.** Track framework, model path/artifact ID, task, classes, version, thresholds, training data provenance, license, and active status. Never permit arbitrary filesystem paths from API input.
- [ ] **T21 — Training/evaluation scripts.** Provide reproducible seed/config/checkpoints/logs for supported task(s); report metrics only from a completed run on an identified dataset split.

### Phase 5 — Metrics and optimization

- [ ] **T22 — Runtime metrics collector.** Measure latency samples, FPS, CPU/RAM, optional GPU, frame drops, reconnects, and inference errors. Include units, measurement windows, and hardware metadata.
- [ ] **T23 — Benchmark script/report template.** Benchmark identical input, warmup, batch, resolution, and model versions; report average and P50/P95/P99 only with enough samples; never insert fabricated numbers.
- [ ] **T24 — ONNX export and parity test.** Export only supported models; compare source/ONNX outputs within explicit tolerances on fixed test inputs; fail validation if output schema or decisions diverge.
- [ ] **T25 — ONNX Runtime benchmark.** Measure on target CPU/GPU and document accuracy/latency/memory trade-offs. Do not claim ONNX is faster without measurements.

### Phase 6 — Frontend operator/admin experience

- [ ] **T26 — Frontend foundation.** Create React + TypeScript + Vite app, lint/typecheck/build scripts, API client, auth/session handling, and responsive industrial visual system.
- [ ] **T27 — Login and role-aware navigation.** Integrate real auth and hide/deny unauthorized actions based on roles; backend remains the authorization source of truth.
- [ ] **T28 — Overview dashboard.** Fetch real aggregate history/metrics and show explicit empty/loading/error states; no hardcoded production statistics.
- [ ] **T29 — Live inspection view.** Show source type, video/frame, browser-side overlays, status, timestamp, model version, FPS/latency, WebSocket state, and fallback/reconnect UX.
- [ ] **T30 — Camera, inspection history, and detail screens.** Use backend data with filter/pagination, evidence viewing, decision rationale, and disconnected/error states.
- [ ] **T31 — Model/evaluation, health, and user administration screens.** Wire to authorized APIs; clearly label unavailable metrics and unvalidated models.

### Phase 7 — Security, deployment, CI, and release

- [ ] **T32 — Security hardening.** Validate uploads and size limits, redact secrets/RTSP credentials, configure explicit CORS, audit sensitive actions, enforce timeouts/connection limits, and check path handling.
- [ ] **T33 — Docker Compose deployment.** Add non-root backend/frontend containers, PostgreSQL, health checks, persistent DB/evidence volumes, environment-based secrets, and no public DB port. Run `docker compose config` and integration tests when Docker is available.
- [ ] **T34 — CI workflow.** Run supported Python lint/format/tests, migration checks, frontend lint/typecheck/build, and dependency checks; pin versions only after compatibility is verified.
- [ ] **T35 — Operations documentation.** Setup, migrations, backup/restore, RTSP-in-Docker caveats, deployment, security notes, model/dataset provenance, known limitations, and demo steps.
- [ ] **T36 — MVP release gate.** Verify image + sample-video path end-to-end with a real available model; persist to PostgreSQL; validate API and frontend; run tests and runtime benchmark; review licenses and secrets. Explicitly list any unmet criteria; do not call it production-ready otherwise.

## Recommended execution order

Work one task at a time, keeping changes small and tested. Phase 1 tasks T02, T03, and T05–T09 have passing local SQLite/unit/API tests. The immediate next task is **T10 — Implement camera source abstraction**, starting with video-file/simulated input. Keep PostgreSQL runtime, actual model inference, and deployment validation as explicit gates.

## Cross-cutting definition of done for every task

1. Add or update automated tests, including failure paths.
2. Run focused tests and relevant broader checks; record exact commands/results.
3. Confirm error paths cannot return a false PASS.
4. Update docs/config examples and known limitations.
5. Inspect Git diff; do not stage local databases, secrets, logs, caches, evidence, or model artifacts unintentionally.
6. Mark the checkbox complete only after verification in this environment.

## Reference repositories

README-level review only; the reference source code and dependency lockfiles have not been audited. Reported results below are not this project's results.

| Repository | README-level technique | Use / caveat |
|---|---|---|
| `Hvora2005/industrial-defect-detection` | YOLO11, FastAPI, OpenCV, SQLite, NEU-DET | Pipeline pattern; verify dataset/model provenance and applicable licenses before adoption. |
| `Medamineelkhattabi/Real-time-object-detection` | React/Vite, FastAPI/Ultralytics, WebSocket, tracking | Transport/overlay concepts; general object recognition is not defect validation. |
| `mtaha-ai/industrial-anomaly-detection` | PatchCore, PaDiM, autoencoder, MVTec AD | Separate anomaly interface; MVTec terms are CC BY-NC-SA 4.0 per README and may limit commercial use. |
| `sjsr-0401/yolo26-industrial-vision` | YOLO/ONNX training and deployment | Workflow inspiration only; named version, dependency compatibility, and reported benchmark were not independently validated. |

## Environment and risks

- Python 3.14.2 and Node 24.13.0 are installed. Docker is not installed in the current environment; Docker deployment cannot yet be executed here.
- `pytest` is not installed. Current 25 tests run via standard-library `unittest`; select/pin a test runner as part of T34.
- Current SQLAlchemy default is SQLite. PostgreSQL operation is not yet validated.
- No industrial model, dataset, physical camera, or GPU has been validated in this workspace.
- Ultralytics/Torch/Anomalib compatibility with Python 3.14 is unknown. Keep optional dependencies isolated until verified.
- Model configuration must include a task-appropriate rejection policy before a successful detection can produce PASS. No anomaly score is derived from object-detection confidence.
