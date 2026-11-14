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
from app.agent.tools import list_tools
from app.core.constants import Role as RoleNames
from app.db.models import AgentApproval, AgentRun, AuditLog, User
from app.db.session import get_db
from app.services.tenant import get_user_org_id, scoped_one, scoped_query

router = APIRouter(prefix="/agent", tags=["agent"])

RUN_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
APPROVE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN)


class AgentChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AgentDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


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
