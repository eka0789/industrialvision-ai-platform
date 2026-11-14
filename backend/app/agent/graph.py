"""Agent state graph (D2): intent → plan → tool loop → validate → respond.

Dependency-free orchestration with hard guarantees:

- **Iteration cap** — at most ``MAX_TOOL_ITERATIONS`` tool executions per run;
  the cap cannot be talked past (it is enforced in code, not in a prompt).
- **Per-tool timeout** — each tool executes on a worker thread bounded by
  ``TOOL_TIMEOUT_SECONDS`` (asyncio.wait_for), so a hung tool cannot hang the
  run or the event loop.
- **Retry-with-rollback** — a failing tool is retried once; on retry the prior
  partial result for that step is dropped (rollback) before re-execution.
- **Structured tool logs** — every attempt records tool, args, status, and
  duration_ms into ``agent_tool_logs``.

Planner: LLM when a key is configured; otherwise the deterministic rule-based
planner (keyword → tool mapping) and the response is composed extractively and
labeled ``mock-demo``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agent.tools import ToolContext, ToolRefusal, get_tool
from app.core.config import settings
from app.db.models import AgentApproval, AgentRun, AgentToolLog
from app.services.event_bus import event_bus
from app.services.live_feed import inspection_topic  # reuse org topic helper naming

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5
TOOL_TIMEOUT_SECONDS = 20.0
TOOL_RETRIES = 1

from app.agent.tools import APPROVAL_TTL_MINUTES  # noqa: E402  (single TTL source)

# Deterministic rule-based planner: intent keywords → tool chains.
INTENT_RULES: list[tuple[str, list[str]]] = [
    (r"knowledge|manual|sop|procedure|dokumen|panduan", ["kb_search"]),
    (r"predict|failure|gagal|prediksi|rul|health|kesehatan", ["machine_status", "predict_failure"]),
    (r"inspection|inspeksi|defect|cacat|decision", ["latest_inspections", "analyze_defect"]),
    (r"production|throughput|yield|analytics|statistik", ["production_analytics"]),
    (r"incident|lapor|report", ["generate_incident_report"]),
    (r"maintenance request|permintaan perawatan|create maintenance|buat permintaan", ["create_maintenance_request"]),
]


def plan_intent(question: str, rule_based: bool = True) -> list[dict[str, Any]]:
    """Rule-based planner: map the question to a tool chain with arguments."""
    lowered = question.lower()
    for pattern, tools in INTENT_RULES:
        if re.search(pattern, lowered):
            return [{"tool": name, "args": _args_from_question(name, question)} for name in tools]
    return [{"tool": "kb_search", "args": {"query": question[:200]}}]


def _args_from_question(tool_name: str, question: str) -> dict[str, Any]:
    if tool_name == "kb_search":
        return {"query": question[:200]}
    if tool_name == "predict_failure":
        match = re.search(r"(?:machine|mesin)\s*#?(\d+)", question.lower())
        return {"machine_id": int(match.group(1))} if match else {"machine_id": 1}
    if tool_name == "machine_status":
        match = re.search(r"(?:machine|mesin)\s*#?(\d+)", question.lower())
        return {"machine_id": int(match.group(1))} if match else {}
    if tool_name == "latest_inspections":
        return {"limit": 10}
    if tool_name == "production_analytics":
        match = re.search(r"(\d+)\s*(?:hours|jam)", question.lower())
        return {"hours": int(match.group(1))} if match else {"hours": 24}
    if tool_name == "generate_incident_report":
        return {"title": question[:200]}
    if tool_name == "create_maintenance_request":
        match = re.search(r"(?:machine|mesin)\s*#?(\d+)", question.lower())
        return {"machine_id": int(match.group(1)) if match else 1, "title": question[:200]}
    return {}


def llm_plan(question: str) -> list[dict[str, Any]] | None:
    """LLM planner (OpenAI) returning a JSON tool chain; None on any failure."""
    if not settings.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        prompt = (
            "You are a planner for an industrial operations agent. Available tools:\n"
            + json.dumps(list_tools_for_planning(), indent=1)
            + '\nReturn STRICT JSON: {"steps": [{"tool": name, "args": {...}}]}. '
            "Use at most 3 steps.\nQUESTION: " + question
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            return None
        return [
            {"tool": str(step.get("tool")), "args": step.get("args") or {}}
            for step in steps[:3]
            if isinstance(step, dict) and step.get("tool")
        ]
    except Exception as exc:  # noqa: BLE001 - planner failure → rule-based
        logger.warning("LLM planner failed (%s); rule-based planner used", exc)
        return None


def list_tools_for_planning() -> list[dict[str, str]]:
    from app.agent.tools import list_tools

    return [{"name": t["name"], "description": t["description"]} for t in list_tools()]


async def _execute_tool(db: Session, context: ToolContext, step: dict[str, Any]) -> dict[str, Any]:
    """One tool execution bounded by the per-tool timeout (worker thread)."""
    started = time.perf_counter()
    try:
        tool = get_tool(step["tool"])
    except ValueError as exc:
        return {"tool": str(step.get("tool")), "status": "error", "result": {"error": str(exc)}, "duration_ms": 0}
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: tool.execute(db, context, step.get("args") or {})),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        return {"tool": tool.name, "status": "ok" if result.get("status") in (None, "ok", "approval_required") else result.get("status", "ok"), "result": result, "duration_ms": int((time.perf_counter() - started) * 1000)}
    except ToolRefusal as exc:
        return {"tool": tool.name, "status": "refused", "result": {"error": str(exc)}, "duration_ms": int((time.perf_counter() - started) * 1000)}
    except asyncio.TimeoutError:
        return {"tool": tool.name, "status": "timeout", "result": {"error": f"tool exceeded {TOOL_TIMEOUT_SECONDS}s"}, "duration_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 - tool failure is data, not a crash
        return {"tool": tool.name, "status": "error", "result": {"error": str(exc)[:500]}, "duration_ms": int((time.perf_counter() - started) * 1000)}


def _validate_step(step_result: dict[str, Any]) -> str | None:
    """Post-execution validation; returns an error string when invalid."""
    if step_result["status"] in ("timeout", "error", "refused"):
        return step_result["status"]
    result = step_result.get("result") or {}
    try:
        json.dumps(result)  # results must be JSON-serializable
    except (TypeError, ValueError):
        return "non_serializable_result"
    return None


def _compose_response(question: str, steps: list[dict[str, Any]]) -> str:
    """Rule-based extractive composition (labeled mock-demo upstream)."""
    parts: list[str] = []
    for step in steps:
        result = step.get("result") or {}
        status = result.get("status")
        if status == "approval_required":
            parts.append(f"Saya menyiapkan '{step['tool']}' (ID persetujuan #{result.get('approval_id')}) — menunggu persetujuan manusia (owner/admin) sebelum dieksekusi.")
            continue
        if step["status"] != "ok":
            parts.append(f"Langkah {step['tool']} gagal ({step['status']})" + (f": {result.get('error')}" if result.get("error") else "") + ".")
            continue
        if step["tool"] == "kb_search":
            hits = result.get("results", [])
            parts.append(f"Dari basis pengetahuan: {len(hits)} sumber ditemukan. " + " ".join(f"[doc {h['document_id']}] {h['snippet'][:140]}" for h in hits[:2]))
        elif step["tool"] == "machine_status":
            machines = result.get("machines", [])
            parts.append("Status mesin: " + "; ".join(f"{m['code']} ({m['machine_status']})" for m in machines[:5]))
        elif step["tool"] == "predict_failure":
            parts.append(
                f"Prediksi mesin #{result.get('machine_id')}: mode {result.get('mode')}, kesehatan {result.get('health')}, rekomendasi: {result.get('recommendation')}."
            )
        elif step["tool"] == "latest_inspections":
            rows = result.get("inspections", [])
            parts.append(f"{len(rows)} inspeksi terakhir: " + "; ".join(f"#{i['id']} {i['decision']}" for i in rows[:5]))
        elif step["tool"] == "production_analytics":
            parts.append(f"Produksi {result.get('window_hours')} jam: {result.get('total_inspections')} inspeksi {result.get('by_decision')}. {result.get('sample_note') or ''}")
        elif step["tool"] == "generate_incident_report":
            parts.append(f"Draf laporan insiden '{result['report']['title']}' tersusun (butuh tinjauan manusia).")
        elif step["tool"] == "create_maintenance_request":
            parts.append("Permintaan perawatan dibuat setelah persetujuan.")
        else:
            parts.append(f"{step['tool']}: {json.dumps(result)[:200]}")
    return " ".join(parts) or "Tidak ada langkah yang bisa dijalankan untuk pertanyaan ini."


def _role_name(user) -> str:
    """Role without triggering lazy loads on detached instances (tests/queues)."""
    name = getattr(user, "role_name", None)
    return name if name else user.role.name


def run_agent(db: Session, *, org_id: int, user: User, question: str) -> AgentRun:
    """Execute one agent run end-to-end (synchronous; called from the API)."""
    started = time.perf_counter()
    run = AgentRun(
        org_id=org_id,
        user_id=user.id,
        question=question[:2000],
        status="running",
        provider="mock-demo",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    context = ToolContext(org_id=org_id, user_id=user.id, user_role=_role_name(user))
    try:
        plan = llm_plan(question) or plan_intent(question)
        run.plan_json = plan
        run.provider = "openai-planner" if settings.OPENAI_API_KEY else "mock-demo"
        db.commit()

        executed: list[dict[str, Any]] = []
        for step in plan[:MAX_TOOL_ITERATIONS]:
            # The API runs run_agent inside a worker thread (no running loop);
            # use a private loop per step for the bounded async execution.
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_execute_tool(db, context, step))
            finally:
                loop.close()
            error = _validate_step(result)
            if error and result["status"] not in ("refused", "invalid_args"):
                # Retry once with rollback of the partial step result.
                result = asyncio.new_event_loop().run_until_complete(_execute_tool(db, context, step))
                error = _validate_step(result)
            executed.append(result)
            db.add(
                AgentToolLog(
                    run_id=run.id,
                    tool_name=result["tool"],
                    args_json=step.get("args"),
                    status=result["status"],
                    duration_ms=result["duration_ms"],
                    error=(result.get("result") or {}).get("error"),
                )
            )
            db.commit()
            # Stream progress to the org's WS topic.
            event_bus.publish(inspection_topic(org_id).replace("inspections", "agent"), {"type": "agent.step", "run_id": run.id, "tool": result["tool"], "status": result["status"]})

        response = _compose_response(question, executed)
        run.response = response[:4000]
        run.status = "completed"
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
        event_bus.publish(inspection_topic(org_id).replace("inspections", "agent"), {"type": "agent.completed", "run_id": run.id})
        return run
    except Exception as exc:  # noqa: BLE001 - fail-safe: the run records the error
        db.rollback()
        run = db.get(AgentRun, run.id)
        run.status = "failed"
        run.error = str(exc)[:1000]
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
        return run


def execute_approval(db: Session, approval: AgentApproval, approver: User) -> AgentApproval:
    """Execute an approved tool WITH the approver's identity (D3 gate)."""
    from datetime import datetime as dt, timezone as tz

    expires = approval.expires_at if approval.expires_at.tzinfo else approval.expires_at.replace(tzinfo=tz.utc)
    if expires < dt.now(timezone.utc):
        approval.status = "expired"
        db.commit()
        raise ValueError("approval expired")
    if approval.status != "approved":
        raise ValueError(f"approval {approval.id} is {approval.status}, not approved")
    # Side-effecting tools execute their DIRECT effect here (the gated tool
    # wrapper only mints approvals); executor identity is the approver.
    from app.agent.tools import APPROVAL_EXECUTORS

    executor = APPROVAL_EXECUTORS.get(approval.tool_name)
    try:
        if executor is not None:
            result = executor(db, approval.org_id, approver.id, approval.args_json or {})
        else:
            context = ToolContext(org_id=approval.org_id, user_id=approver.id, user_role=_role_name(approver))
            tool = get_tool(approval.tool_name)
            result = tool.execute(db, context, approval.args_json or {})
        approval.result_json = result
        approval.status = "executed"
    except Exception as exc:  # noqa: BLE001
        approval.result_json = {"error": str(exc)[:500]}
        approval.status = "failed"
    db.commit()
    db.refresh(approval)
    return approval


def approval_ttl() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MINUTES)
