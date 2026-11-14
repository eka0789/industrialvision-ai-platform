# Changelog

## 2026-11-14 — Unified Industrial AI Platform (Phases A–G, H in progress)

### Infrastructure (Phase A)
- Redis pub/sub event bus with explicit in-memory fallback; `/health` reports
  the active bus mode and degrades visibly when a configured bus disconnects.
- Worker layer: one task registry, RQ + in-process transports, retries with
  linear backoff, dead-letter records, standalone `python -m app.worker`,
  compose `worker` service.
- Prometheus + Grafana in Compose with provisioned dashboards, `iv_*`
  namespaced metrics, and alert rules (DB/bus/worker/5xx/latency).
- Backup automation: evidence tarball + SHA-256 manifest (schema v2) +
  retention pruning + dry-run; restore verifies checksums first.
- httpOnly-cookie session mode (`AUTH_TOKEN_STORAGE=cookie`) with fail-closed
  double-submit CSRF; body mode unchanged and still the default.
- Multi-tenancy: `organizations` root, `org_id` on every tenant table,
  org-scoped query layer, RBAC v2 (owner/admin/ai_engineer/operator/viewer),
  cross-tenant denial tests for every router, org-scoped demo seed.

### Predictive Maintenance (Phase B)
- machines / sensor_readings / maintenance_requests / maintenance_schedules
  (org-scoped, retention policy).
- AI4I 2020 synthetic-mirror dataset with real-CSV auto-use, hashed splits.
- Training pipelines reusing the run lifecycle (3-candidate selection on
  validation, held-out test metrics only) registering into the model registry
  with SHA-256 checksums.
- Fail-safe inference: `model` / `rules-only-fallback` / `error` modes.
- `/pdm` API with approval-gated maintenance workflow; MaintenancePage UI.

### Knowledge AI (Phase C)
- RAG storage (pgvector-or-numpy adapter), hardened ingestion on the worker
  queue (all-or-nothing), embedding provider abstraction with labeled local
  fallback, hybrid BM25+vector RRF retrieval, grounded answers with
  test-enforced citations and prompt-injection defense, KnowledgePage UI.

### Operations Agent (Phase D)
- 8 org-scoped tools with execution-time RBAC; state graph with iteration
  caps, per-tool timeouts, retry+rollback, structured logs; persisted
  human-in-the-loop approvals (TTL, self-approval denial, approver-identity
  execution); AgentPage workspace.

### MLOps (Phase E)
- Model stage lifecycle (development/staging/production) with evaluation gate,
  one-production rule, explicit rollback; PSI drift detection vs training-time
  reference stats (`iv_model_drift_psi`); optional MLflow mirroring;
  MonitoringPage.

### Vision deepening (Phase F)
- Authenticated MJPEG streaming (no tokens in URLs; honest MJPEG-not-WebRTC
  documentation); optional OCR service with labeled unavailable mode;
  anomaly memory-bank training through the run lifecycle; worker-rendered
  JSON/CSV reports with below-minimum honesty flags.

### ML Laboratory (Phase G)
- Browser-only Q-learning maintenance simulator (explicitly educational);
  read-only experiment browser with artifact checksums.

### Testing
- Backend suite grew from 239 to **376 tests** (unittest, SQLite); CI jobs
  cover the new suites; frontend `tsc + vite build` and oxlint clean; the
  axe/390px e2e sweep covers all new routes.

### Known limitations (see docs/OPERATIONS.md §8)
- Compose smoke executed on this workstation (see PLANNING.md Phase H record);
  TLS uses a self-signed cert in the smoke only.
- Real PostgreSQL CI integration job extended but exercised in CI, not here.
- No real fleet data, physical cameras, GPU workloads, or production LLM keys
  were involved; all such paths are labeled and fail safe.
