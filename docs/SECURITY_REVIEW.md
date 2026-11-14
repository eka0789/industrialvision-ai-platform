# Security Review — New Attack Surface (Phases A–G)

Scope: modules added while extending the platform into the unified industrial
AI platform. Every row lists the enforced control and the test that proves it.
This document is the audit-coverage matrix required by the release gate (H3).

## 1. Attack-surface matrix

| Surface | Threat | Control (enforced where) | Proof (test) |
|---|---|---|---|
| File ingestion (RAG upload) | Wrong type / polyglot | MIME allowlist + extension match + magic-byte signature (PDF `%PDF-`, DOCX `PK`) + binary-in-text sniff + size cap (`app/rag/ingestion.validate_upload`) | `test_rag.py::test_rejects_unknown_type_and_signature_mismatch`, `test_rag_api.py::test_upload_rejects_disallowed_types` |
| File ingestion (RAG upload) | Oversized file | `RAG_MAX_UPLOAD_BYTES` read-limit + explicit 413 | `test_rag_api.py` (413 asserted) |
| Report download | Path traversal | Filename regex `org\d+-(daily|weekly|monthly)-\w+\.(json|csv)` + org-prefix check (`app/api/v1/routers/reports.py`) | `test_vision_deepening.py::ReportsTests` |
| Report files | Foreign-org read | Org-prefix filter on listing and download (foreign names 404) | `test_vision_deepening.py::test_csv_format_and_cross_tenant_denial` |
| Agent tools | Unauthorized execution | Execution-time RBAC (`ToolRefusal`), org predicate on every query (`app/agent/tools.py`) | `test_agent.py::test_tool_execution_requires_role_at_runtime`, `test_tools_are_org_scoped` |
| Agent tools | Side effects without approval | Side-effecting tools only mint `AgentApproval`; direct executors run solely post-approval with the approver's identity | `test_agent.py::test_side_effecting_tool_requires_approval`, `test_approval_gate_executes_as_approver` |
| Agent approvals | Self-approval, expiry bypass, double-spend | Self-approval 403; expiry checked before status; non-pending → 409; one-time execution | `test_agent.py::AgentApprovalFlowTests`, `AgentApiTests::test_approval_workflow_via_api` |
| Agent planner (LLM) | Prompt injection via KB content | Retrieval delimiting + sentence-level sanitization + output neutralization (`app/rag/answers.py`) | `test_rag.py::test_prompt_injection_sanitized_from_context` |
| RAG answers | Fabricated citations | Every `[n]` validated against retrieved chunk ids; violation = `CitationError` | `test_rag.py::test_citation_validation_rejects_hallucinated_indices` |
| Chat / ingest endpoints | Automated abuse | Per-user chat rate limit (12/min, 429); uploads gated by validation | `test_rag_api.py::test_chat_rate_limit` |
| PdM predictions | Garbage on bad input | Non-finite features → `error` mode; corrupt artifact → `error` (checksum), never silent rules fallback | `test_pdm.py::PdmInferenceTests` |
| Model registry | Unverified promotion | Production stage requires completed-run evaluation on identified split; one production per (org, task) | `test_mlops.py::StageLifecycleTests` |
| MJPEG stream | Token leakage in URLs | Bearer via Authorization header only (fetch-based player); no query tokens | `test_vision_deepening.py::MjpegStreamTests::test_stream_requires_auth` |
| Training-run browser | Directory traversal | Path separators rejected (422), routing denies traversal; read-only | `test_lab.py::test_single_run_lookup_and_invalid_ids` |
| Cookie sessions (A5) | CSRF on unsafe methods | Double-submit token (`X-CSRF-Token` == `iv_csrf` cookie), fail-closed middleware; httpOnly token cookies; SameSite scoping | `test_cookie_sessions.py` (mode matrix) |
| Multi-tenancy (A6) | Cross-tenant read/write | `org_id` on all 13 tenant tables; `scoped_query` predicate everywhere; foreign ids 404 (no existence leak); org-scoped bus topics | `test_multi_tenant.py` (13 tests) |
| Workers/queue (A2) | Silent work loss | Retry + dead-letter records; enqueue degradation visible in `/health` | `test_task_queue.py` |
| Redis (A1) | Credential leakage in logs | URL password redacted (`_safe_url`) | code review + `test_event_bus_redis.py` |

## 2. SSRF review

- No module fetches user-supplied URLs. The LLM/embedding providers use
  fixed vendor endpoints configured via env keys — no webhook-style tools
  exist and **URL-fetching agent tools are forbidden by design** (the tool
  registry enumerates a fixed allowlist; `tools.py` is the only registration
  path).
- `REDIS_URL`, `MLFLOW_TRACKING_URI`, `DATABASE_URL` are operator-provided
  server-side configuration, not request inputs.

## 3. Rate limits

| Endpoint | Limit | Notes |
|---|---|---|
| `/rag/chat` | 12 requests / 60 s / user | In-process counter; production fronting via nginx `limit_req` recommended for IP-level control |
| `/rag/documents` upload | Size cap + validation (no count limit) | Documented follow-up: per-user ingest quota |
| `/agent/chat` | Iteration cap (5 tools) + per-tool timeout (20 s) | Bounded worst-case runtime |
| Login | Account lockout (existing) | Unchanged |

## 4. Audit coverage (state-changing actions)

| Action | Audit event |
|---|---|
| Login/logout/lockout | `auth.*` (existing) |
| Machine create/update | `pdm.machine.created` / `pdm.machine.updated` |
| Sensor reading recorded | `pdm.reading.recorded` |
| Maintenance request lifecycle | `pdm.request.created/approved/rejected/completed/cancelled` |
| Schedule created | `pdm.schedule.created` |
| RAG upload/delete | `rag.document.uploaded` / `rag.document.deleted` |
| Agent chat / approvals | `agent.chat` / `agent.approval.approved` / `agent.approval.rejected` |
| Report generate/delete | `report.generated` / `report.deleted` |
| User management | `admin.user.created` / `admin.user.status_changed` |

## 5. Residual risks (accepted, documented)

1. Chat rate limiting is per-process (Redis-backed limiting recommended at
   multi-replica scale).
2. RAG ingestion has no per-user quota (size/type limits only).
3. The CSRF cookie is intentionally JS-readable (double-submit design); the
   session token cookies remain httpOnly — see OPERATIONS.md §7.1.
4. MJPEG is not WebRTC (latency budget documented); no exposure beyond the
   authenticated fetch path.
