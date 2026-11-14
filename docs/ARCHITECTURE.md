# Architecture — Unified Industrial AI Platform

The platform extends the security-hardened vision-inspection core into a
unified industrial AI platform. The original boundaries are unchanged; new
modules attach to the same FastAPI core, database, auth, and tenancy rules.

```
                    ┌────────────────────────────────────────────────────┐
   Browser ─ TLS ─  │  frontend (nginx, React 19 SPA, WCAG 2.1 AA)       │
                    │  pages: Overview · Inspections · Cameras · Models  │
                    │  Maintenance · Knowledge · Agent · Monitoring ·    │
                    │  Lab · Reports · Admin                             │
                    └───────────────┬────────────────────────────────────┘
                                    │ HTTPS (bearer or httpOnly-cookie mode)
                    ┌───────────────▼────────────────────────────────────┐
                    │  backend (FastAPI, modular monolith)               │
                    │  ├─ vision core (inspection, decision engine)      │
                    │  ├─ pdm/        predictive maintenance             │
                    │  ├─ rag/        knowledge AI (hybrid retrieval)    │
                    │  ├─ agent/      operations agent (HITL approvals)  │
                    │  ├─ drift/      PSI drift vs training reference    │
                    │  ├─ reports     JSON/CSV exports                   │
                    │  └─ event_bus (memory ⇄ Redis pub/sub, /health)    │
                    └───┬──────────────┬───────────────┬─────────────────┘
                        │              │               │
              ┌─────────▼───┐   ┌──────▼─────┐   ┌─────▼──────────┐
              │ PostgreSQL  │   │ Redis      │   │ worker (RQ)    │
              │ (+pgvector  │   │ pub/sub +  │   │ same task fns  │
              │  when ext.) │   │ queue      │   │ retries+DLQ    │
              └─────────────┘   └──────┬─────┘   └─────┬──────────┘
                                       │        ┌──────▼──────────┐
                              ┌────────▼────────▼───┐  ┌──────────┐
                              │ prometheus ─ grafana│  │ volumes: │
                              │ (alerts, dashboards)│  │ evidence │
                              └─────────────────────┘  │ models   │
                                                       └──────────┘
```

## Principles that did not change

- **Fail-safe everything**: inference/training failures produce
  `ERROR`/`failed`, never false PASS or fabricated numbers. Every ML surface
  declares its mode (`model` / `rules-only-fallback` / `mock-demo` / `error`).
- **Tenant isolation**: `organizations` is the tenancy root; all 13+ tenant
  tables carry `org_id`; every query is org-scoped; cross-tenant access is
  denial-tested per router (`tests/test_multi_tenant.py`).
- **RBAC v2**: owner / admin / ai_engineer / operator / viewer — enforced in
  dependencies, re-checked at agent-tool execution time.
- **Verified done**: every task records the exact commands and results in
  PLANNING.md.

## Data flow highlights

- Inspections publish to org-scoped bus topics (`inspections:org:<id>`);
  the WS live feed subscribes per-user-org — fan-out never crosses tenants.
- RAG ingestion, PdM report rendering, and sensor-retention pruning run on
  the same task-queue transports as the video-inspection task (in-process
  pool in dev, RQ worker in production).
- Drift: training pipelines persist per-feature reference statistics on the
  registry row; a scheduled task computes PSI and exposes
  `iv_model_drift_psi{model,feature}` to Prometheus.
- Agent side-effecting tools require a persisted human approval; execution
  runs with the approver's identity (never the requester's).

## Module map

| Path | Purpose |
|---|---|
| `backend/app/pdm/` | dataset mirror, training, fail-safe inference |
| `backend/app/rag/` | embeddings, ingestion, hybrid retrieval, grounded answers |
| `backend/app/agent/` | tool registry, state graph, approvals |
| `backend/app/services/task_queue.py` | RQ + in-process transports, dead-letter |
| `backend/app/services/event_bus.py` | memory ⇄ Redis bus with explicit modes |
| `backend/app/services/drift.py` | PSI computation |
| `backend/app/services/metrics.py` | `iv_*` Prometheus series |
| `deploy/prometheus`, `deploy/grafana` | scrape config, alert rules, dashboard |
