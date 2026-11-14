"""Agent tool registry (D1) — Pydantic-validated, org-scoped, RBAC-checked.

Every tool executes with an explicit :class:`ToolContext` carrying the caller
(user + org). RBAC is checked at EXECUTION time, not just plan time: the graph
may plan a tool the caller is not allowed to run, and the tool refuses.
Side-effecting tools (``create_maintenance_request``) never act directly —
they mint a persisted :class:`AgentApproval` and return
``{"status": "approval_required"}`` (D3).
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.constants import Role as RoleNames
from app.db.models import Camera, Inspection, Machine, User
from app.services.tenant import get_user_org_id


class ToolContext(BaseModel):
    org_id: int
    user_id: int
    user_role: str


class ToolRefusal(RuntimeError):
    """Raised when the caller lacks the role for a tool at execution time."""


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        args_model: type[BaseModel],
        result_model: type[BaseModel] | None,
        allowed_roles: tuple[str, ...],
        fn: Callable[[Session, ToolContext, BaseModel], dict[str, Any]],
    ) -> None:
        self.name = name
        self.description = description
        self.args_model = args_model
        self.result_model = result_model
        self.allowed_roles = allowed_roles
        self.fn = fn

    def execute(self, db: Session, context: ToolContext, raw_args: dict[str, Any]) -> dict[str, Any]:
        if context.user_role not in self.allowed_roles:
            raise ToolRefusal(f"role '{context.user_role}' may not run {self.name}")
        try:
            args = self.args_model.model_validate(raw_args)
        except ValidationError as exc:
            return {"status": "invalid_args", "errors": exc.errors(include_url=False)}
        return self.fn(db, context, args)


APPROVAL_TTL_MINUTES = 60

_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args_model: type[BaseModel], result_model: type[BaseModel] | None = None,
         allowed_roles: tuple[str, ...] = RoleNames.all()):
    def decorator(fn):
        _REGISTRY[name] = Tool(name, description, args_model, result_model, allowed_roles, fn)
        return fn

    return decorator


def get_tool(name: str) -> Tool:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown tool {name!r}; available: {sorted(_REGISTRY)}") from None


def list_tools() -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "roles": list(t.allowed_roles)}
        for t in _REGISTRY.values()
    ]


# ── Argument models (Pydantic-validated inputs) ─────────────────────────────


class EmptyArgs(BaseModel):
    pass


class KbSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class MachineStatusArgs(BaseModel):
    machine_id: int | None = Field(default=None, gt=0)


class LatestInspectionsArgs(BaseModel):
    camera_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=10, ge=1, le=50)


class PredictFailureArgs(BaseModel):
    machine_id: int = Field(gt=0)


class AnalyzeDefectArgs(BaseModel):
    inspection_id: int = Field(gt=0)


class ProductionAnalyticsArgs(BaseModel):
    hours: int = Field(default=24, ge=1, le=720)


class IncidentReportArgs(BaseModel):
    inspection_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)


class CreateMaintenanceRequestArgs(BaseModel):
    machine_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")


# ── Tool implementations (org-scoped by construction) ───────────────────────


@tool("kb_search", "Search the organization knowledge base for procedures and manuals.", KbSearchArgs)
def kb_search(db: Session, context: ToolContext, args: KbSearchArgs) -> dict[str, Any]:
    from app.rag.retrieval import retrieve

    hits = retrieve(db, org_id=context.org_id, query=args.query, top_k=args.top_k)
    return {
        "status": "ok",
        "results": [
            {"chunk_id": hit.chunk_id, "document_id": hit.document_id, "page": hit.page, "snippet": hit.content[:300]}
            for hit in hits
        ],
    }


@tool("machine_status", "List machines and their current status for the organization.", MachineStatusArgs)
def machine_status(db: Session, context: ToolContext, args: MachineStatusArgs) -> dict[str, Any]:
    machines: list[Machine]
    if args.machine_id:
        row = db.query(Machine).filter(Machine.org_id == context.org_id, Machine.id == args.machine_id).first()
        machines = [row] if row else []
    else:
        machines = (
            db.query(Machine)
            .filter(Machine.org_id == context.org_id)
            .order_by(Machine.id)
            .limit(100)
            .all()
        )
    return {
        "status": "ok",
        "machines": [
            {"id": m.id, "name": m.name, "code": m.code, "machine_status": m.status, "type": m.machine_type}
            for m in machines
        ],
    }


@tool("latest_inspections", "Fetch the latest inspection results (optionally per camera).", LatestInspectionsArgs)
def latest_inspections(db: Session, context: ToolContext, args: LatestInspectionsArgs) -> dict[str, Any]:
    from app.services.inspection_serializer import serialize_inspection

    query = db.query(Inspection).filter(Inspection.org_id == context.org_id)
    if args.camera_id:
        query = query.filter(Inspection.camera_id == args.camera_id)
    rows = query.order_by(Inspection.created_at.desc(), Inspection.id.desc()).limit(args.limit).all()
    return {
        "status": "ok",
        "inspections": [
            {
                "id": row.id,
                "camera_id": row.camera_id,
                "decision": row.decision,
                "timestamp": serialize_inspection(row)["timestamp"].isoformat() if row.created_at else None,
                "product_id": row.product_id,
            }
            for row in rows
        ],
    }


@tool("predict_failure", "Run the fail-safe PdM prediction for one machine.", PredictFailureArgs)
def predict_failure(db: Session, context: ToolContext, args: PredictFailureArgs) -> dict[str, Any]:
    from app.pdm.inference import predict_for_machine

    machine = db.query(Machine).filter(Machine.org_id == context.org_id, Machine.id == args.machine_id).first()
    if machine is None:
        return {"status": "not_found", "error": f"machine {args.machine_id} not found in this organization"}
    result = predict_for_machine(db, machine)
    result.pop("features", None)
    return result


@tool("analyze_defect", "Summarize one inspection's detections, anomaly score, and decision.", AnalyzeDefectArgs)
def analyze_defect(db: Session, context: ToolContext, args: AnalyzeDefectArgs) -> dict[str, Any]:
    inspection = db.query(Inspection).filter(Inspection.org_id == context.org_id, Inspection.id == args.inspection_id).first()
    if inspection is None:
        return {"status": "not_found", "error": f"inspection {args.inspection_id} not found in this organization"}
    return {
        "status": "ok",
        "inspection": {
            "id": inspection.id,
            "decision": inspection.decision,
            "error_status": inspection.error_status,
            "detections": inspection.detection_results_json,
            "anomaly": inspection.anomaly_score_json,
            "latency_ms": inspection.latency_ms,
        },
    }


@tool("production_analytics", "Aggregate inspection decisions over the last N hours.", ProductionAnalyticsArgs)
def production_analytics(db: Session, context: ToolContext, args: ProductionAnalyticsArgs) -> dict[str, Any]:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    rows = (
        db.query(Inspection.decision, func.count(Inspection.id))
        .filter(Inspection.org_id == context.org_id, Inspection.created_at >= since)
        .group_by(Inspection.decision)
        .all()
    )
    counts = {decision: int(count) for decision, count in rows}
    total = sum(counts.values())
    return {
        "status": "ok",
        "window_hours": args.hours,
        "total_inspections": total,
        "by_decision": counts,
        "sample_note": None if total >= 30 else f"only {total} samples in window — below the 30-sample minimum, treat as indicative",
    }


@tool("generate_incident_report", "Assemble a structured incident report from inspection data.", IncidentReportArgs)
def generate_incident_report(db: Session, context: ToolContext, args: IncidentReportArgs) -> dict[str, Any]:
    from datetime import datetime, timezone

    inspection_block = None
    if args.inspection_id:
        inspection = db.query(Inspection).filter(Inspection.org_id == context.org_id, Inspection.id == args.inspection_id).first()
        if inspection is None:
            return {"status": "not_found", "error": f"inspection {args.inspection_id} not found"}
        inspection_block = {
            "id": inspection.id,
            "decision": inspection.decision,
            "error_status": inspection.error_status,
            "camera_id": inspection.camera_id,
            "product_id": inspection.product_id,
        }
    return {
        "status": "ok",
        "report": {
            "title": args.title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "org_id": context.org_id,
            "requested_by_user_id": context.user_id,
            "inspection": inspection_block,
            "note": "structured draft for human review — not an official quality record",
        },
    }


APPROVAL_EXECUTORS: dict[str, Callable[[Session, int, int, dict[str, Any]], dict[str, Any]]] = {}


def _do_create_maintenance_request(db: Session, org_id: int, executor_user_id: int, args_data: dict[str, Any]) -> dict[str, Any]:
    """Direct executor — runs ONLY after a human approval (D3)."""
    from app.db.models import MaintenanceRequest
    from app.services.tenant import stamp_org

    args = CreateMaintenanceRequestArgs.model_validate(args_data)
    machine = db.query(Machine).filter(Machine.org_id == org_id, Machine.id == args.machine_id).first()
    if machine is None:
        return {"status": "not_found", "error": f"machine {args.machine_id} not found in this organization"}
    row = MaintenanceRequest(
        machine_id=machine.id,
        requested_by_user_id=executor_user_id,
        title=args.title,
        priority=args.priority,
        status="pending",
    )
    stamp_org(row, User(org_id=org_id, id=executor_user_id))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "ok", "request_id": row.id, "machine_id": row.machine_id, "title": row.title}


APPROVAL_EXECUTORS["create_maintenance_request"] = _do_create_maintenance_request


@tool(
    "create_maintenance_request",
    "Create a maintenance request — REQUIRES human approval before it is executed.",
    CreateMaintenanceRequestArgs,
    allowed_roles=(RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER),
)
def create_maintenance_request(db: Session, context: ToolContext, args: CreateMaintenanceRequestArgs) -> dict[str, Any]:
    from datetime import datetime, timedelta, timezone

    from app.db.models import AgentApproval

    machine = db.query(Machine).filter(Machine.org_id == context.org_id, Machine.id == args.machine_id).first()
    if machine is None:
        return {"status": "not_found", "error": f"machine {args.machine_id} not found in this organization"}
    approval = AgentApproval(
        org_id=context.org_id,
        tool_name="create_maintenance_request",
        args_json=args.model_dump(),
        requested_by_user_id=context.user_id,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MINUTES),
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return {
        "status": "approval_required",
        "approval_id": approval.id,
        "note": "a human approver (owner/admin) must approve before the request is created",
    }
