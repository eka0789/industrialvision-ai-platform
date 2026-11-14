"""Quality & maintenance report exports (F4).

Daily/weekly/monthly inspection-decision and maintenance summaries generated
on the worker queue as JSON/CSV files under ``EVIDENCE_ROOT/reports``, org-
scoped filenames, RBAC-gated (viewer read, operator+ generate, admin delete).
Honest reporting rules apply: windows with <30 inspections are flagged as
below-minimum rather than presented as quality statistics.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.config import settings
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, Inspection, MaintenanceRequest, User
from app.db.session import get_db
from app.services.task_queue import task_queue
from app.services.tenant import get_user_org_id

router = APIRouter(prefix="/reports", tags=["reports"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
GENERATE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER)
DELETE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN)

PERIODS = {"daily": 1, "weekly": 7, "monthly": 30}
MIN_SAMPLES = 30


class ReportRequest(BaseModel):
    period: str = Field(pattern="^(daily|weekly|monthly)$")
    format: str = Field(default="json", pattern="^(json|csv)$")
    kinds: list[str] = Field(default=["quality", "maintenance"])


def reports_root() -> Path:
    return Path(settings.EVIDENCE_ROOT) / "reports"


def build_report_payload(db: Session, org_id: int, period: str) -> dict:
    """Aggregate one period window (synchronous aggregation, bounded queries)."""
    days = PERIODS[period]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(Inspection.decision, func.count(Inspection.id))
        .filter(Inspection.org_id == org_id, Inspection.created_at >= since)
        .group_by(Inspection.decision)
        .all()
    )
    by_decision = {decision: int(count) for decision, count in rows}
    total = sum(by_decision.values())
    maintenance_rows = (
        db.query(MaintenanceRequest.status, func.count(MaintenanceRequest.id))
        .filter(MaintenanceRequest.org_id == org_id, MaintenanceRequest.created_at >= since)
        .group_by(MaintenanceRequest.status)
        .all()
    )
    by_status = {s: int(c) for s, c in maintenance_rows}
    return {
        "kind": "quality+maintenance",
        "period": period,
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "org_id": org_id,
        "total_inspections": total,
        "inspections_by_decision": by_decision,
        "maintenance_requests_by_status": by_status,
        "below_minimum": total < MIN_SAMPLES,
        "below_minimum_note": (
            f"only {total} inspections in window (<{MIN_SAMPLES}) — treat as an operational count, not a quality statistic"
            if total < MIN_SAMPLES
            else None
        ),
    }


def write_report_file(org_id: int, period: str, fmt: str, payload: dict) -> str:
    """Persist a report file with an org-scoped name; returns the filename."""
    root = reports_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    name = f"org{org_id}-{period}-{stamp}.{fmt}"
    path = root / name
    if fmt == "json":
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["metric", "value"])
        writer.writerow(["period", payload["period"]])
        writer.writerow(["generated_at", payload["generated_at"]])
        writer.writerow(["total_inspections", payload["total_inspections"]])
        writer.writerow(["below_minimum", payload["below_minimum"]])
        for decision, count in sorted(payload["inspections_by_decision"].items()):
            writer.writerow([f"inspections_{decision.lower()}", count])
        for status_name, count in sorted(payload["maintenance_requests_by_status"].items()):
            writer.writerow([f"maintenance_{status_name}", count])
        path.write_text(buffer.getvalue(), encoding="utf-8")
    return name


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_report(
    payload: ReportRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*GENERATE_ROLES)),
) -> dict:
    org_id = get_user_org_id(user)
    # Aggregate now (bounded), render on the worker thread, enqueue-safe.
    report_payload = build_report_payload(db, org_id, payload.period)
    task_queue.enqueue("generate_report_file", org_id, payload.period, payload.format, report_payload)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=org_id,
            action="report.generated",
            details_json={"period": payload.period, "format": payload.format},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {"queued": True, "period": payload.period, "format": payload.format}


@router.get("")
def list_reports(
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict]:
    """List THIS org's report files (prefix-scoped, path-traversal safe)."""
    prefix = f"org{get_user_org_id(_user)}-"
    root = reports_root()
    if not root.is_dir():
        return []
    items = []
    for path in sorted(root.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True):
        items.append(
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items


@router.get("/download/{filename}", response_class=None)
def download_report(
    filename: str,
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> Response:
    """Download one report file; foreign-org filenames simply do not exist."""
    if not re.fullmatch(r"org\d+-(daily|weekly|monthly)-\w+\.(json|csv)", filename):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid report filename")
    expected_prefix = f"org{get_user_org_id(_user)}-"
    if not filename.startswith(expected_prefix):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    path = reports_root() / filename
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    media = "application/json" if filename.endswith(".json") else "text/csv"
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{filename}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELETE_ROLES)),
) -> Response:
    expected_prefix = f"org{get_user_org_id(user)}-"
    if not filename.startswith(expected_prefix) or "/" in filename or ".." in filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    path = reports_root() / filename
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    path.unlink()
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="report.deleted",
            details_json={"filename": filename},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
