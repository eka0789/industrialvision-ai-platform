# IndustrialVision AI — Implementation Backlog

**Status:** In progress — MVP is not production-ready
**Workspace checked:** 2026-10-24
**Runtime checked:** Python 3.12.3 (dependencies installed into workspace `.venv`; system `site-packages` is root-owned, a workspace `.pth` links the preinstalled numpy/OpenCV), Node 24.13.0. Docker and `pytest` are unavailable in the current environment.
**GitHub:** `https://github.com/eka0789/industrialvision-ai-platform`

This backlog breaks the master prompt into small, testable tasks. Each item should be implemented in a focused change, validated, documented, and only then marked done. “Done” means verified in this workspace, not inferred from code or a demo.

## Current verified baseline

- The project has a FastAPI/SQLAlchemy backend with Alembic migrations, JWT auth + RBAC, camera CRUD + session APIs, video/camera source abstraction, bounded capture worker with reconnect/backoff/metrics, video-file inspection with codec validation and frame aggregation, WebSocket endpoints with auth, anomaly detector interface (score/map/threshold, non-probability), PatchCore/PaDiM adapters with compatibility gating, anomaly evaluation (image/pixel AUROC, AUPRC, F1, PRO).
- Decision engine implements fail-safe PASS/FAIL/REVIEW/ERROR. Decisions without configured defect/anomaly policy return REVIEW.
- Inspection manager persists inference results and decision reasons; errors recorded as ERROR. PASS requires configured rejection or anomaly policy.
- `python -m unittest discover -s tests -v` from `backend/` passed 89 tests: config, auth/RBAC, SQLite migrations, inference contracts, YOLO/ONNX adapters, image API, history/detail, evidence storage, decision persistence, camera CRUD/sessions/RBAC, video/camera sources, capture worker, anomaly interfaces/adapters/evaluation, WebSocket auth. `python -m compileall -q app migrations tests` passed.
- Image API and OpenAPI routes wired; inference persists actual adapter results. Unavailable model config = ERROR. YOLO/ONNX adapter behavior unit-tested; no industrial model/dataset or Ultralytics runtime validated. PostgreSQL runtime, frontend, Docker not verified.
- Four reference repositories’ public pages/READMEs reviewed; source/lockfiles/artifacts not audited. No reference code copied.
- **Re-verified 2026-10-24 (Python 3.12.3):** `python -m unittest discover -s tests` from `backend/` passed **137 tests** (89 baseline + 48 Phase 4) and `python -m compileall -q app migrations tests` passed. Baseline repair included: `EvidenceStorage.resolve` now checks containment/existence without `strict=True`, so path traversal raises `ValueError` (instead of leaking `FileNotFoundError` on Python 3.12).
- **Phase 4 verified:** dataset validation/split/leakage tooling with a machine-readable JSON report (`python -m app.datasets.cli`, 16 tests); dataset setup guide sourced from provider pages (`docs/DATASETS.md`); model registry metadata API under `/models` with migration `a00000000003` (upgrade/downgrade/re-upgrade verified), RBAC, and MODEL_ROOT-only artifact references — absolute paths, traversal, missing files, and unknown extensions are rejected (10 tests); reproducible training/evaluation entry points (`app.training.train`, `app.training.evaluate`) that write metrics only from a completed run on an identified, hashed dataset split (22 tests incl. subprocess CLI runs).
- **Phase 4 not validated:** no real public dataset was downloaded; no Ultralytics training ran (the missing-runtime path is tested: run marked failed, no metrics file); PostgreSQL, real model inference, and deployment remain unvalidated.
- **T23 verified (2026-10-24):** benchmark protocol + report template; full suite passes **205 tests** (174 + 31 benchmark) and `python -m compileall -q app migrations tests` passes. With no real model artifact here, the verified CLI path is the failure gate (exit 2, no report file); the protocol logic — statistics gating, warmup exclusion, input-identity/mutation checks, report-write guards, artifact checksum verification — is unit-tested with stub engines. Real timings must come from runs against a registered, checksum-verified model on target hardware.
- **T22 verified (2026-10-24):** runtime metrics collector with units, measurement windows, and hardware metadata; full suite passes **174 tests** (137 Phase 4 + 35 collector + 2 wiring) and `python -m compileall -q app migrations tests` passes. psutil, pynvml, and torch are absent in this workspace, so the psutil backend was verified with a stub, GPU correctly reports `available: false`, and the procfs backend produced the CPU/RAM numbers here. No GPU or real camera workload was measured.

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

- [x] **T10 — Implement camera source abstraction.** Support simulated/video-file source first, then webcam and RTSP. Clearly distinguish source types and redact RTSP credentials from API output/logs/errors.
- [x] **T11 — Implement bounded capture worker.** Use bounded/latest-frame buffering, read timeout, no-frame detection, exponential-backoff reconnect, connection limits, metrics, and graceful shutdown. Cover disconnect/reconnect with mocked sources.
- [x] **T12 — Add video-file inspection.** Validate codec/container and upload limits; process frames at configurable intervals; aggregate per-frame results without losing timestamps; persist evidence and report dropped frames/FPS/latency.
- [x] **T13 — Add camera administration and session APIs.** Protect camera create/update/delete/start/stop with RBAC; persist status, last error, resolution, FPS, and last inspection. Never return stored camera secrets.

### Phase 3 — Real-time events and anomaly detection

- [x] **T14 — Add WebSocket inspection events.** Authenticate/authorize sockets, send metadata and detection results rather than every full-resolution frame, handle disconnects, and test reconnect/fallback behavior.
- [x] **T15 — Add anomaly detector interface.** Keep anomaly detection distinct from object detection. Define score/map contracts and threshold metadata; uncalibrated scores must not be called probabilities.
- [x] **T16 — Add PatchCore/PaDiM adapter only after compatibility checks.** Isolate optional dependencies, document supported Python/Torch versions, train memory bank/distribution from normal samples, and gate unsupported runtime environments cleanly.
- [x] **T17 — Add anomaly evaluation.** Implement image/pixel AUROC and localization metrics only with compatible ground truth; test metric functions on known/synthetic fixtures and document dataset license restrictions.

### Phase 4 — Dataset and model lifecycle

- [x] **T18 — Dataset validation and split tooling.** `app.datasets` validates image/label pairing, class IDs, detection boxes and mask polygons, empty/corrupt/undecodable files, duplicate stems, and orphan labels; performs a deterministic split grouped by source identity (content SHA-256 + filename stem) so leakage cannot occur by construction, and detects identity leakage across pre-split directories. Emits a machine-readable JSON report with structured issues, split files, per-file checksums, and a stable dataset fingerprint. Verified by 16 tests in `tests/test_dataset_validation.py` (incl. CLI exit codes 0/1/2).
- [x] **T19 — Dataset setup guide.** `docs/DATASETS.md` documents provider pages read on 2026-10-24: MVTec AD / LOCO and KolektorSDD (CC BY-NC-SA 4.0, commercial restricted), DAGM 2007 Zenodo record (CC BY 4.0, provider MD5 checksums), NEU-DET and GC10-DET (**no license published — treat as uncleared**), plus download steps, expected YOLO-style layout, and local checksum recording. No dataset or annotation file is redistributed in the repo; tests use synthetic fixtures explicitly labeled synthetic.
- [x] **T20 — Model registry and metadata API.** `/models` list/detail/create/patch/activate/deactivate plus `/models/{id}/evaluations`. Tracks framework, artifact reference with **server-computed SHA-256 checksum and artifact ID**, task, classes, version, thresholds, training-data provenance, license, and active status (at most one active model per task type). Writes require `admin`/`ai_engineer`; evaluation records must be a completed run with an identified split. API input is only a relative artifact reference resolved strictly under MODEL_ROOT — absolute paths, `..` traversal, missing files, and extensions outside `.onnx/.pt/.pth` are rejected with 422 before any DB write. Migration `a00000000003` verified upgrade → downgrade → re-upgrade. 10 tests in `tests/test_model_registry_api.py`.
- [x] **T21 — Training/evaluation scripts.** `app.training.train` requires an integer seed, a passing dataset validation report (root must match), and an identified split; records config snapshot, environment versions, log, and checkpoints in one run directory; sets Python/NumPy/Torch seeds; and refuses to finish unless a checkpoint **and** metrics exist. Missing Ultralytics (as here) → run marked `failed` with reason, exit 2, **no metrics file**. `app.training.evaluate` refuses any run that is not `completed` (exit 3), requires ground truth for every image in a hashed split file (exit 4), and writes an evaluation report binding metrics to the run's dataset identity + split SHA-256. `TrainingRun` enforces lifecycle transitions and a `publish_metrics()` gate; detection metrics (precision/recall/F1/per-class AP/mAP) are pure Python with known-value fixtures. 22 tests in `tests/test_training_runs.py`, incl. subprocess runs of both CLIs. Actual Ultralytics training and real-data evaluation remain unvalidated.

### Phase 5 — Metrics and optimization

- [x] **T22 — Runtime metrics collector.** `app.services.runtime_metrics` records capture and inference latency samples in bounded windows (ms; P50/P95/P99 withheld until ≥10 samples), windowed FPS over a sliding seconds window, frame drops, reconnects, and inference errors with truncated messages, plus cumulative counters and drop/error rates. Every numeric metric declares its unit in a `UNITS` registry of dotted paths (tests assert full coverage), each snapshot carries its measurement-window metadata (started_at, elapsed, window sizes) and hardware metadata (platform, Python, CPU model/cores, RAM), and resource sampling uses psutil when installed with a Linux `/proc` fallback — the active backend is always named and missing sources report `null`, never invented numbers. GPU sampling is optional (pynvml → torch.cuda) and reports `available: false` when absent. Wired into the capture worker (optional collector; live camera streaming uses the process-wide singleton), video inspection (snapshot attached to the result and returned by `POST /cameras/{camera_id}/inspect-video`), and `process_camera_frame` (inference latency/errors; non-finite latency now yields ERROR instead of being persisted). 35 tests in `tests/test_runtime_metrics.py` plus 2 wiring tests in `tests/test_inspection_manager.py`.
- [x] **T23 — Benchmark script/report template.** `app.benchmark` (`python -m app.benchmark.cli --model-id …`) benchmarks a registered model on an identical input: input identity is a SHA-256 over decoded pixels verified **before and after** the run (a mutating engine fails the run instead of silently breaking comparability), warmup iterations are excluded from every statistic, batch size is recorded with `batch_size != 1` rejected rather than simulated, and resolution plus registry model identity (id, version, **server-verified artifact SHA-256**, config incl. thresholds) are recorded with hardware metadata. Average/P50/P95/P99 are withheld (`null` + explicit `stats_withheld_reason`) below `min_samples_for_stats` (default 30); sample count, min/max, and raw `samples_ms` are always reported as observed facts. `write_report()` refuses non-completed reports and stats violating the minimum-sample rule; failed runs exit 2 with **no report file** (T21 parity). `docs/BENCHMARK.md` documents the controlled-variable protocol, the JSON report template, a comparison table, and the no-fabricated-numbers rules. 31 tests in `tests/test_benchmark.py` (incl. a subprocess CLI failure run). Real artifact timing on real hardware remains unvalidated — no model artifact exists in this workspace.
- [x] **T24 — ONNX export and parity test.** Export only supported models; compare source/ONNX outputs within explicit tolerances on fixed test inputs; fail validation if output schema or decisions diverge.
- [x] **T25 — ONNX Runtime benchmark.** Measure on target CPU/GPU and document accuracy/latency/memory trade-offs. Do not claim ONNX is faster without measurements.

### Phase 6 — Frontend operator/admin experience

- [x] **T26 — Frontend foundation.** Create React + TypeScript + Vite app, lint/typecheck/build scripts, API client, auth/session handling, and responsive industrial visual system.
- [x] **T27 — Login and role-aware navigation.** Integrate real auth and hide/deny unauthorized actions based on roles; backend remains the authorization source of truth.
- [x] **T28 — Overview dashboard.** Fetch real aggregate history/metrics and show explicit empty/loading/error states; no hardcoded production statistics.
- [x] **T29 — Live inspection view.** Show source type, video/frame, browser-side overlays, status, timestamp, model version, FPS/latency, WebSocket state, and fallback/reconnect UX.
- [x] **T30 — Camera, inspection history, and detail screens.** Use backend data with filter/pagination, evidence viewing, decision rationale, and disconnected/error states.
- [x] **T31 — Model/evaluation, health, and user administration screens.** Wire to authorized APIs; clearly label unavailable metrics and unvalidated models.

### Phase 7 — Security, deployment, CI, and release

- [x] **T32 — Security hardening.** Validate uploads and size limits, redact secrets/RTSP credentials, configure explicit CORS, audit sensitive actions, enforce timeouts/connection limits, and check path handling.
- [x] **T33 — Docker Compose deployment.** Add non-root backend/frontend containers, PostgreSQL, health checks, persistent DB/evidence volumes, environment-based secrets, and no public DB port. Run `docker compose config` and integration tests when Docker is available.
- [x] **T34 — CI workflow.** Run supported Python lint/format/tests, migration checks, frontend lint/typecheck/build, and dependency checks; pin versions only after compatibility is verified.
- [x] **T35 — Operations documentation.** Setup, migrations, backup/restore, RTSP-in-Docker caveats, deployment, security notes, model/dataset provenance, known limitations, and demo steps.
- [x] **T36 — MVP release gate.** Verify image + sample-video path end-to-end with a real available model; persist to PostgreSQL; validate API and frontend; run tests and runtime benchmark; review licenses and secrets. Explicitly list any unmet criteria; do not call it production-ready otherwise.

## Recommended execution order

Work one task at a time, keeping changes small and tested. Phases 1–5 (T01–T23) are implemented and locally verified: 205 tests pass on SQLite with mocked inference. The immediate next task is **T24 — ONNX export and parity test** (Phase 5). Keep PostgreSQL runtime, actual model inference/training on a real dataset, and deployment validation as explicit gates.

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

- Python 3.12.3 (this workspace) and Node 24.13.0 are installed. Dependencies live in the workspace `.venv` (system site-packages is root-owned; a `.pth` file links the preinstalled numpy/OpenCV). Docker is not installed; Docker deployment cannot yet be executed here.
- `pytest` is not installed. Tests run via standard-library `unittest` (205 passed, 2026-10-24); select/pin a test runner as part of T34.
- psutil/pynvml/torch are not installed: runtime resource metrics fall back to Linux `/proc`, and GPU metrics report unavailable. Install psutil to enable its backend; it is intentionally not a required dependency.
- Current SQLAlchemy default is SQLite. PostgreSQL operation is not yet validated.
- No industrial model, dataset, physical camera, or GPU has been validated in this workspace. No public dataset has been downloaded here; `docs/DATASETS.md` documents sources and licenses only.
- Ultralytics/Torch/Anomalib compatibility with Python 3.12 is unknown; the training entry point treats Ultralytics as optional and its missing-runtime path is tested (failed run, no metrics). Keep optional dependencies isolated until verified.
- Model configuration must include a task-appropriate rejection policy before a successful detection can produce PASS. No anomaly score is derived from object-detection confidence.

---

# Unified Industrial AI Platform — Master Plan (Phases A–H)

**Started:** 2026-11-14. Baseline: 239 tests (2 env-dependent WS failures repaired first — see below).
Non-negotiable principles from the master prompt apply: verified-done only, no fabricated numbers,
fail-safe always, security bar never drops, tenant isolation sacred, small verified steps.

## Phase A — Scaling & infrastructure foundation

- [x] **A1 — Redis event bus with explicit in-memory fallback.** `app/services/event_bus.py`
  now ships two interchangeable implementations behind one interface: `EventBus` (in-process,
  unchanged) and `RedisEventBus` (Redis pub/sub; sync publish, one background pump task feeding
  the same bounded per-subscriber queues, automatic reconnect with resubscribe and backoff).
  While Redis is unreachable publishes degrade to local-only delivery and `connected` is False —
  payloads are never silently dropped and the outage is observable. `REDIS_URL` setting added
  (empty = memory mode); production config validator now refuses to start without
  `redis://`/`rediss://` (cross-process fan-out is required, not optional). `/health` reports
  `event_bus: {mode, connected, configured}` and marks overall status degraded when a configured
  bus is disconnected. Compose: hardened `redis:7-alpine` service (no public port, no-new-privileges,
  cap_drop ALL, tmpfs data, noeviction) wired into backend env + health-gated depends_on.
  First repair of this phase: the two positive-path `/ws/inspections/live` tests failed in a fresh
  workspace because the WS endpoints used the module-global `SessionLocal` (empty file DB) instead
  of the `get_db` dependency the tests override — endpoints now inject `get_db` (FastAPI supports
  DI on WebSocket routes), production behavior unchanged.
  Verified: `python -m unittest tests.test_event_bus tests.test_event_bus_redis tests.test_health
  tests.test_config tests.test_live_feed tests.test_websockets tests.test_seed_demo` → 48 tests OK
  (fake-transport tests: fan-out across two bus instances = two simulated processes, exactly-once
  local delivery, degraded local delivery on outage, ImportError fallback, pump reconnect after
  transport failure, failed ping reporting; 3 consecutive green runs). `redis` 6.4.0 installed and
  pinned `redis>=6.0,<7` in requirements-core.txt; `.env.example` documents REDIS_URL.

- [x] **A2 — Worker layer (RQ + in-process).** `app/services/task_queue.py`: one registry of
  named task functions (`app/services/tasks.py` — first task: `video_inspection`, same function
  the synchronous API uses), two transports: `InProcessTaskQueue` (bounded thread pool, linear-
  backoff retries, bounded dead-letter record after final failure, graceful shutdown) and
  `RQTaskQueue` (RQ over Redis, `Retry(max=3)`, failed-job registry as the dead-letter store).
  `TaskQueueFacade` degrades visibly to in-process execution when an RQ enqueue fails (health
  shows `worker.degraded`) — work is delayed, never dropped. `python -m app.worker` is the
  standalone RQ worker (SIGTERM-safe). `TASK_QUEUE_MODE=in-process|rq|auto` added (production
  validator requires `rq`); `/health` reports `worker: {mode, degraded, dead_letter_count}`.
  Compose gains a hardened `worker` service (same image, shared evidence/model volumes, internal
  network only). Verified: `python -m unittest tests.test_task_queue` → 12 tests OK (in-process
  execute/retry/recover/dead-letter/shutdown-drain; RQ via fakeredis: execute, retry→dead registry,
  unknown-task rejection; facade degrade+recover). Full suite: `python -m unittest discover -s tests`
  → **262 tests OK** (4 pre-existing skips). `rq` 2.12.0 pinned `rq>=2.0,<3` in requirements-core;
  `requirements-dev.txt` adds fakeredis (test-only).

- [x] **A3 — Prometheus + Grafana into Compose with namespaced metrics.** `app/services/metrics.py`
  exposes `iv_`-prefixed series on a dedicated registry: HTTP rate/latency (per route template),
  inspection latency histogram, decision outcome counter, WebSocket connections gauge + opened
  counter, and scrape-time component gauges (event bus connected, worker degraded, dead-lettered
  tasks, DB reachable — read from the live objects at scrape, never cached). `MetricsMiddleware`
  counts every request; decision recording is wired at both success and `_finish_error` paths; WS
  gauges update on open/close. `/metrics` is unauthenticated by design and documented as
  internal-network-only (nginx never proxies it). Compose adds hardened `prom/prometheus` (scrapes
  backend + self, 15d retention, non-root uid, cap_drop ALL, 127.0.0.1:9090) and `grafana` (provisioned
  datasource + dashboard, signup disabled, admin password required via env, 127.0.0.1:3001) plus
  `deploy/prometheus/alerts.yml` (DB down, bus down, worker degraded, dead-letter increase, 5xx rate,
  slow inspections). Verified: `python -m unittest tests.test_metrics` → 9 tests OK (exposition names,
  counter/gauge behavior, middleware route-template counting, alert rules YAML parse incl. required
  alerts, dashboard JSON parse, datasource URL). Full suite 271 tests OK. `docker compose config`
  validates all 7 services (postgres, redis, backend, frontend, prometheus, grafana, worker) with
  required env set — Docker IS available on this workstation, so H1 compose execution is possible.
  prometheus-client pinned `>=0.21,<1`.

- [x] **A4 — Evidence volume backup automation.** `scripts/backup.sh` now does database dump +
  optional evidence tarball (`--evidence`) in one run, writes a schema-versioned JSON manifest
  (v2: dump sha256, size, evidence root/archive/sha256/file_count, retention_days) generated via
  an embedded Python block (no heredoc quoting bugs), prunes complete old backup sets by
  `--retention-days` (0 = keep), and supports `--dry-run` which validates preconditions and writes
  nothing (not even the output directory). `restore` re-hashes the dump against the manifest and
  refuses on mismatch; evidence archives restore with file-count verification instructions.
  `docs/OPERATIONS.md` §5 rewritten: manifest schema + quarterly timed restore drill.
  Verified: `python -m unittest tests.test_backup_script` → 5 tests OK from `backend/` (dry-run
  writes nothing & needs no pg tools; full backup with stub pg_dump produces dump+evidence+valid
  manifest with correct sha256/file_count; retention prunes old sets and `--retention-days 0`
  keeps them; missing pg_dump fails real mode). Note: tests pass Git Bash paths (`_posix`) because
  MSYS tar treats `C:\...` as a remote host; in Linux/containers plain paths work.

- [x] **A5 — httpOnly-cookie session mode + CSRF.** `AUTH_TOKEN_STORAGE=body|cookie` (default
  `body` = exactly the previous behavior; API clients unaffected). Cookie mode: login sets
  `iv_access` (httpOnly, SameSite=Lax, `/`), `iv_refresh` (httpOnly, SameSite=Strict, path-scoped
  to `/auth/refresh`), and `iv_csrf` (readable by JS for double-submit) — bodies carry empty token
  strings plus `token_storage: "cookie"` so clients can pick their strategy. `get_current_user`
  falls back to the access cookie (header wins). `CsrfMiddleware` (no-op in body mode) rejects
  unsafe methods in cookie mode unless `X-CSRF-Token` matches the `iv_csrf` cookie, fail-closed,
  with only pre-session endpoints (`/auth/login`, `/auth/refresh`, `/health`, `/metrics`, `/`)
  exempt. Silent refresh: cookie mode reads the rotated token from the cookie; rotation semantics
  unchanged (old token dead after use). Logout revokes + clears all three cookies. `Secure` cookies
  automatic in production (`AUTH_COOKIE_SECURE` override). Threat-model notes added to
  docs/OPERATIONS.md §7.1 (mechanism, exemptions rationale, residual risks: login-CSRF, JS-readable
  CSRF cookie by design, WS unaffected). Verified: `python -m unittest tests.test_cookie_sessions
  tests.test_auth tests.test_refresh_tokens tests.test_auth_security` → 26 tests OK (mode matrix:
  body mode returns tokens & sets no cookies & needs no CSRF; cookie mode e2e login → me → CSRF
  rejection without/with wrong header → pass with correct header → silent refresh via cookie with
  rotation → stale-token 401 → logout clears cookies; safe methods exempt). Full suite 286 OK.

- [x] **A6 — Multi-tenancy.** `organizations` table added; `org_id` (NOT NULL, server_default 1,
  indexed, FK to organizations) on ALL 13 tenant tables (users, production_lines, cameras,
  model_versions, model_evaluations, inspection_sessions, inspections, detection_results,
  anomaly_results, system_events, login_attempts, refresh_tokens, audit_logs). Migration
  `a00000000005` (batch_alter_table for SQLite compatibility): create org table → bootstrap org id 1
  → add nullable column per table → backfill to 1 → NOT NULL → index + server_default; downgrade
  reverses. Verified upgrade→downgrade→base + re-upgrade via the existing migration test on SQLite;
  PostgreSQL coverage is asserted by the CI integration job (H5 runs it). Org-scoped query layer
  (`app/services/tenant.py`: `scoped_query`/`scoped_one`/`stamp_org` — foreign ids simply 404, no
  existence leak). RBAC v2 roles: owner/admin/ai_engineer/operator/viewer (`app/core/constants.py`),
  read endpoints accept viewer; writes owner/admin(/operator); admin endpoints owner+admin only,
  only owners mint owner/admin. Routers scoped: cameras (CRUD/start/stop/sessions/inspect-video),
  models (list/detail/patch/activate/deactivate/evaluations + per-org active-model rule + per-org
  name uniqueness), inspections (list/detail/evidence + create stamps org), admin (org-scoped user
  list/invite/status with audit logs `admin.user.created`, `admin.user.status_changed`). Live feed
  + WS fan-out org-scoped: bus topics are now `inspections:org:<org_id>` (`inspection_topic`),
  inspection payloads carry `org_id`. Seed: default org created, owner/viewer demo accounts added
  (owner/Owner!Vision2026, viewer/Viewer!Vision2026), still production-refused. CLI bootstrap
  stamps org 1.
  Verified: `python -m unittest tests.test_multi_tenant` → 13 tests OK (org-scoped listings for
  cameras/models/inspections/admin-users; cross-tenant GET/PUT/DELETE/start/stop/session/activate/
  evidence/status all 404; org-stamped creation; cross-org model-name non-collision; owner-mint
  403; audit-log assertion; org-scoped bus topics). Full suite **299 tests OK** (4 skips).
