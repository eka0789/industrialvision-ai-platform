"""AI Operations Agent API (D4): chat runs, history, tool logs, approvals.

Run history is org-scoped; approvals follow the persisted HITL workflow
(request → pending → approved/rejected → executed/failed) with expiry,
self-approval denial, and full audit logging. Runs execute on the task queue
thread so the per-tool timeouts bound the request; progress streams on the
``agent:org:<id>`` bus topic.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_roles
from app.agent import graph as agent_graph
from app.agent.tools import TOOL_APPROVER_ROLES, ToolContext, get_tool, list_tools
from app.core.constants import Role as RoleNames
from app.db.models import AgentApproval, AgentRun, AuditLog, InspectionRecipe, ProductionBatch, User
from app.db.session import get_db
from app.services.tenant import get_user_org_id, scoped_one, scoped_query

router = APIRouter(prefix="/agent", tags=["agent"])

RUN_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
APPROVE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER)


class AgentChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AgentDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class DefectSpikeEvalRequest(BaseModel):
    defect_category: str = Field(default="seal_leak", min_length=1, max_length=80)
    window_minutes: int = Field(default=5, ge=1, le=120)
    spike_threshold: int = Field(default=3, ge=1, le=500)
    camera_id: int | None = Field(default=None, gt=0)
    auto_propose_approval: bool = True


class IncidentTriageRequest(BaseModel):
    incident_type: str = Field(
        default="FOREIGN_MATTER",
        pattern="^(FOREIGN_MATTER|LENS_BLUR_OR_FOG|PSI_DRIFT|MISSING_EXP_DATE|SEAL_DEFECT)$",
    )
    batch_id: int | None = Field(default=None, gt=0)
    recipe_id: int | None = Field(default=None, gt=0)
    camera_id: int | None = Field(default=None, gt=0)
    zone_id: str = Field(default="roi_1_seal", min_length=1, max_length=120)
    affected_qty: int = Field(default=150, ge=1, le=1_000_000)
    new_threshold: float = Field(default=0.62, ge=0.05, le=0.99)


def _serialize_run(run: AgentRun, with_logs: bool = False) -> dict:
    payload = {
        "id": run.id,
        "question": run.question,
        "status": run.status,
        "provider": run.provider,
        "response": run.response,
        "error": run.error,
        "duration_ms": run.duration_ms,
        "created_at": run.created_at,
    }
    if with_logs:
        payload["tool_logs"] = [
            {
                "tool": log.tool_name,
                "status": log.status,
                "duration_ms": log.duration_ms,
                "error": log.error,
                "args": log.args_json,
            }
            for log in run.tool_logs
        ]
        payload["plan"] = run.plan_json
    return payload


def _serialize_approval(approval: AgentApproval) -> dict:
    return {
        "id": approval.id,
        "tool_name": approval.tool_name,
        "args": approval.args_json,
        "status": approval.status,
        "requested_by_user_id": approval.requested_by_user_id,
        "decided_by_user_id": approval.decided_by_user_id,
        "decision_note": approval.decision_note,
        "expires_at": approval.expires_at,
        "result": approval.result_json,
        "created_at": approval.created_at,
    }


@router.get("/tools")
def tools(_user: User = Depends(get_current_user)) -> list[dict]:
    """Available tools with their RBAC requirements (for the workspace UI)."""
    return list_tools()


@router.post("/chat", status_code=status.HTTP_200_OK)
def chat(
    payload: AgentChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RUN_ROLES)),
) -> dict:
    """One agent turn: plan → tools (bounded, timed) → grounded response."""
    run = agent_graph.run_agent(db, org_id=get_user_org_id(user), user=user, question=payload.question)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="agent.chat",
            details_json={"run_id": run.id, "status": run.status},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return _serialize_run(run, with_logs=True)


@router.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*RUN_ROLES)),
) -> list[dict]:
    rows = scoped_query(db, AgentRun, _user).order_by(AgentRun.created_at.desc()).limit(50).all()
    return [_serialize_run(row) for row in rows]


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*RUN_ROLES)),
) -> dict:
    run = scoped_one(db, AgentRun, _user, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return _serialize_run(run, with_logs=True)


@router.get("/approvals")
def list_approvals(
    approval_status: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVE_ROLES)),
) -> list[dict]:
    query = scoped_query(db, AgentApproval, user)
    if approval_status:
        query = query.filter(AgentApproval.status == approval_status)
    rows = query.order_by(AgentApproval.created_at.desc()).limit(100).all()
    return [_serialize_approval(row) for row in rows]


@router.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: int,
    payload: AgentDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVE_ROLES)),
) -> dict:
    """Approve and EXECUTE the pending tool as the approving identity."""
    approval = scoped_one(db, AgentApproval, user, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Approval is {approval.status}")
    allowed_for_tool = TOOL_APPROVER_ROLES.get(approval.tool_name, (RoleNames.OWNER, RoleNames.ADMIN))
    approver_role = agent_graph._role_name(user)
    if approver_role not in allowed_for_tool:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{approver_role}' may not approve '{approval.tool_name}'; required: {list(allowed_for_tool)}",
        )
    # SQLite loses tzinfo; stored timestamps are UTC.
    from datetime import timezone

    expires = approval.expires_at if approval.expires_at.tzinfo else approval.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        approval.status = "expired"
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval request expired")
    if approval.requested_by_user_id == user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-approval is not allowed")

    approval.status = "approved"
    approval.decided_by_user_id = user.id
    approval.decision_note = payload.note
    approval.decided_at = datetime.now().astimezone()
    db.commit()
    try:
        approval = agent_graph.execute_approval(db, approval, user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="agent.approval.approved",
            details_json={"approval_id": approval.id, "tool": approval.tool_name, "status": approval.status},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return _serialize_approval(approval)


@router.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: int,
    payload: AgentDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVE_ROLES)),
) -> dict:
    approval = scoped_one(db, AgentApproval, user, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Approval is {approval.status}")
    approval.status = "rejected"
    approval.decided_by_user_id = user.id
    approval.decision_note = payload.note
    approval.decided_at = datetime.now().astimezone()
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="agent.approval.rejected",
            details_json={"approval_id": approval.id, "tool": approval.tool_name},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(approval)
    return _serialize_approval(approval)


@router.post("/evaluate-defect-spike", status_code=status.HTTP_200_OK)
def evaluate_defect_spike_endpoint(
    payload: DefectSpikeEvalRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RUN_ROLES)),
) -> dict:
    """Evaluate 5-minute Food Division defect spikes (e.g. seal_leak) with RAG SOP & HITL approval."""
    from app.agent.tools import ToolContext, get_tool

    org_id = get_user_org_id(user)
    ctx = ToolContext(org_id=org_id, user_id=user.id, user_role=agent_graph._role_name(user))
    tool = get_tool("check_defect_spike")
    result = tool.execute(db, ctx, payload.model_dump())
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=org_id,
            action="agent.defect_spike_evaluated",
            details_json={
                "defect_category": payload.defect_category,
                "spike_detected": result.get("spike_detected"),
                "approval_id": result.get("approval_id"),
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return result


@router.post("/triage-incident", status_code=status.HTTP_200_OK)
def triage_food_incident_endpoint(
    payload: IncidentTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RUN_ROLES)),
) -> dict:
    """Proactive Incident Triage (Fase 6): RAG SOP 1:1 citation lookup + Line Quality Summary + HITL Mitigation."""
    from app.db.models import RagDocument
    from app.rag import answers as rag_answers
    from app.rag.food_sop_seed import seed_food_safety_sops

    org_id = get_user_org_id(user)
    role_name = agent_graph._role_name(user)
    ctx = ToolContext(org_id=org_id, user_id=user.id, user_role=role_name)

    # Ensure standard Food Safety SOPs are available in RAG for this organization
    has_docs = db.query(RagDocument).filter(RagDocument.org_id == org_id, RagDocument.status == "ready").first()
    if has_docs is None:
        seed_food_safety_sops(db, org_id=org_id, user_id=user.id)

    sop_queries = {
        "FOREIGN_MATTER": "SOP-FOD-04 Protokol Karantina Batch Kontaminasi Benda Asing FOREIGN_MATTER draft_batch_hold_notice",
        "MISSING_EXP_DATE": "SOP-QA-02 Tindakan Korektif Tanggal Kedaluwarsa Expired Date Hilang MISSING_EXP_DATE Batch Hold",
        "LENS_BLUR_OR_FOG": "SOP-SAN-03 Prosedur Washdown Sanitasi Lensa Kamera LENS_BLUR_OR_FOG Laplacian Variance",
        "PSI_DRIFT": "SOP-SAN-03 PSI data drift pencahayaan propose_recipe_threshold_adjustment CLAHE",
        "SEAL_DEFECT": "SOP-QA-01 Penanganan Deviasi Heat-Seal Kebocoran Kemasan SEAL_DEFECT sealer_temp_c",
    }
    query_text = sop_queries.get(payload.incident_type, sop_queries["FOREIGN_MATTER"])
    grounded = rag_answers.answer_question(db, org_id=org_id, question=query_text, top_k=3)

    summary_tool = get_tool("get_line_quality_summary")
    line_summary = summary_tool.execute(db, ctx, {"batch_id": payload.batch_id} if payload.batch_id else {})

    mitigation_action: dict | None = None
    approval_id: int | None = None

    if role_name != RoleNames.VIEWER:
        if payload.incident_type in ("FOREIGN_MATTER", "MISSING_EXP_DATE"):
            target_batch_id = payload.batch_id or line_summary.get("batch_id")
            if target_batch_id is None:
                latest_batch = (
                    db.query(ProductionBatch)
                    .filter(ProductionBatch.org_id == org_id)
                    .order_by(ProductionBatch.id.desc())
                    .first()
                )
                target_batch_id = latest_batch.id if latest_batch else None
            if target_batch_id is not None:
                hold_tool = get_tool("draft_batch_hold_notice")
                mitigation_action = hold_tool.execute(
                    db,
                    ctx,
                    {
                        "batch_id": target_batch_id,
                        "reason": (
                            f"Triase Proaktif Insiden {payload.incident_type}: "
                            "Wajib karantina lot sesuai SOP-FOD-04 / SOP-QA-02 [1]."
                        ),
                        "affected_qty": payload.affected_qty,
                    },
                )
                approval_id = mitigation_action.get("approval_id")
        elif payload.incident_type in ("PSI_DRIFT", "LENS_BLUR_OR_FOG"):
            target_recipe_id = payload.recipe_id
            if target_recipe_id is None:
                latest_recipe = (
                    db.query(InspectionRecipe)
                    .filter(InspectionRecipe.org_id == org_id)
                    .order_by(InspectionRecipe.is_active.desc(), InspectionRecipe.id.desc())
                    .first()
                )
                target_recipe_id = latest_recipe.id if latest_recipe else None
            if target_recipe_id is not None:
                thr_tool = get_tool("propose_recipe_threshold_adjustment")
                mitigation_action = thr_tool.execute(
                    db,
                    ctx,
                    {
                        "recipe_id": target_recipe_id,
                        "zone_id": payload.zone_id,
                        "new_threshold": payload.new_threshold,
                        "justification": (
                            f"Kompensasi otomatis insiden {payload.incident_type} sesuai SOP-SAN-03 Pasal 3 [1]."
                        ),
                    },
                )
                approval_id = mitigation_action.get("approval_id")
        elif payload.incident_type == "SEAL_DEFECT":
            spike_tool = get_tool("check_defect_spike")
            mitigation_action = spike_tool.execute(
                db,
                ctx,
                {
                    "defect_category": "seal_leak",
                    "window_minutes": 5,
                    "spike_threshold": 3,
                    "camera_id": payload.camera_id,
                    "auto_propose_approval": True,
                },
            )
            approval_id = mitigation_action.get("approval_id")

    db.add(
        AuditLog(
            user_id=user.id,
            org_id=org_id,
            action="agent.incident_triage",
            details_json={
                "incident_type": payload.incident_type,
                "approval_id": approval_id,
                "citations_count": len(grounded.citations),
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {
        "status": "approval_required" if approval_id else "ok",
        "incident_type": payload.incident_type,
        "sop_answer": grounded.answer,
        "sop_provider": grounded.provider,
        "sop_citations": grounded.citations,
        "line_quality_summary": line_summary,
        "mitigation_action": mitigation_action,
        "approval_id": approval_id,
    }
