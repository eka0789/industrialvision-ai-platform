"""Food Safety & HACCP Traceability: Production Batch / Lot Management,
Smart Evidence Retention, QA Supervisor Digital Signature, and PDF/CSV Audit Reports (Fase 2).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.constants import Role as RoleNames
from app.db.models import (
    AuditLog,
    Inspection,
    InspectionRecipe,
    Organization,
    ProductSKU,
    ProductionBatch,
    ProductionLine,
    User,
)
from app.db.session import get_db
from app.services.evidence_retention import cleanup_expired_pass_evidence
from app.services.tenant import get_user_org_id, scoped_one, scoped_query, stamp_org

router = APIRouter(prefix="/batches", tags=["food-traceability-batches"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
WRITE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER)
QA_SIGN_ROLES = (RoleNames.OWNER, RoleNames.ADMIN)


class BatchCreate(BaseModel):
    batch_code: str = Field(min_length=3, max_length=120)
    sku_id: int | None = Field(default=None, gt=0)
    sku_code: str | None = Field(default=None, min_length=2, max_length=80)
    recipe_id: int | None = Field(default=None, gt=0)
    line_id: int | None = Field(default=None, gt=0)
    line_name: str | None = Field(default=None, max_length=120)
    shift: str = Field(default="SHIFT_1", min_length=1, max_length=40)
    target_quantity: int | None = Field(default=None, ge=1, le=10_000_000)
    target_qty: int = Field(default=1000, ge=1, le=10_000_000)


class BatchSignRequest(BaseModel):
    qa_notes: str = Field(default="Audit verified compliant with HACCP / ISO 22000 / BPOM standards", max_length=2000)
    quarantine: bool = False


class RetentionPruneRequest(BaseModel):
    pass_retention_days: int = Field(default=7, ge=1, le=3650)
    audit_retention_days: int = Field(default=365, ge=30, le=3650)


def _batch_or_404(db: Session, user: User, batch_id: int) -> ProductionBatch:
    row = scoped_one(db, ProductionBatch, user, batch_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production batch not found")
    return row


def _serialize_batch(db: Session, row: ProductionBatch) -> dict[str, Any]:
    computed_total = (row.total_pass or 0) + (row.total_fail or 0) + (row.total_review or 0)
    total = max(int(row.total_inspected or 0), computed_total)
    reject_rate_pct = round(((row.total_fail or 0) / total) * 100.0, 2) if total > 0 else 0.0
    yield_pct = round(((row.total_pass or 0) / total) * 100.0, 2) if total > 0 else 100.0

    default_defects = {
        "SEAL_DEFECT": 0,
        "MISSING_EXP_DATE": 0,
        "BURNT_OR_BROKEN": 0,
        "FOREIGN_MATTER": 0,
        "seal_leak": 0,
        "missing_exp_date": 0,
        "foreign_matter": 0,
        "burnt": 0,
    }
    raw_defects = dict(row.defect_counts_json or {})
    merged_defects = {**default_defects, **raw_defects}
    # Synchronize uppercase and lowercase defect counters
    merged_defects["SEAL_DEFECT"] = max(int(merged_defects.get("SEAL_DEFECT", 0)), int(merged_defects.get("seal_leak", 0)))
    merged_defects["seal_leak"] = max(int(merged_defects.get("seal_leak", 0)), int(merged_defects.get("SEAL_DEFECT", 0)))
    merged_defects["MISSING_EXP_DATE"] = max(int(merged_defects.get("MISSING_EXP_DATE", 0)), int(merged_defects.get("missing_exp_date", 0)))
    merged_defects["missing_exp_date"] = max(int(merged_defects.get("missing_exp_date", 0)), int(merged_defects.get("MISSING_EXP_DATE", 0)))
    merged_defects["BURNT_OR_BROKEN"] = max(int(merged_defects.get("BURNT_OR_BROKEN", 0)), int(merged_defects.get("burnt", 0)))
    merged_defects["burnt"] = max(int(merged_defects.get("burnt", 0)), int(merged_defects.get("BURNT_OR_BROKEN", 0)))
    merged_defects["FOREIGN_MATTER"] = max(int(merged_defects.get("FOREIGN_MATTER", 0)), int(merged_defects.get("foreign_matter", 0)))
    merged_defects["foreign_matter"] = max(int(merged_defects.get("foreign_matter", 0)), int(merged_defects.get("FOREIGN_MATTER", 0)))

    org_obj = db.get(Organization, row.org_id)
    org_name = org_obj.name if org_obj else f"Organization #{row.org_id}"

    sku_obj = db.get(ProductSKU, row.sku_id) if row.sku_id else None
    sku_name = sku_obj.name if sku_obj else row.sku_code

    operator_name = None
    if row.started_by:
        op_user = db.get(User, row.started_by)
        operator_name = op_user.username if op_user else f"user#{row.started_by}"

    signer_name = None
    if row.qa_signed_by_user_id:
        signer = db.get(User, row.qa_signed_by_user_id)
        signer_name = signer.username if signer else f"user#{row.qa_signed_by_user_id}"

    return {
        "id": row.id,
        "org_id": row.org_id,
        "organization_name": org_name,
        "batch_code": row.batch_code,
        "sku_id": row.sku_id,
        "sku_code": row.sku_code,
        "sku_name": sku_name,
        "recipe_id": row.recipe_id,
        "line_id": row.line_id,
        "line_name": row.line_name or (f"Line #{row.line_id}" if row.line_id else "Line 01"),
        "shift": row.shift,
        "started_by": row.started_by,
        "operator_id": row.started_by,
        "operator_username": operator_name,
        "status": row.status,
        "target_quantity": row.target_quantity,
        "target_qty": row.target_quantity,
        "total_inspected": total,
        "total_pass": row.total_pass or 0,
        "total_fail": row.total_fail or 0,
        "total_review": row.total_review or 0,
        "first_pass_yield_pct": yield_pct,
        "yield_pct": yield_pct,
        "reject_rate_pct": reject_rate_pct,
        "defect_counts": merged_defects,
        "qa_signed_by_user_id": row.qa_signed_by_user_id,
        "qa_signed_by_username": signer_name,
        "qa_signature_hash": row.qa_signature_hash,
        "qa_signed_at": row.qa_signed_at.isoformat() if row.qa_signed_at else None,
        "qa_notes": row.qa_notes,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "ended_at": row.closed_at.isoformat() if row.closed_at else None,
    }


def _render_simple_pdf(lines: list[str]) -> bytes:
    """Build a valid, self-contained PDF-1.4 document with Helvetica text."""
    escaped_ops = ["BT", "/F1 10 Tf", "45 790 Td", "14 TL"]
    for idx, line in enumerate(lines[:54]):
        safe = (
            line.encode("ascii", errors="replace")
            .decode("ascii")
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        if idx == 0:
            escaped_ops.append(f"({safe}) Tj")
        else:
            escaped_ops.append(f"T* ({safe}) Tj")
    escaped_ops.append("ET")
    stream_bytes = "\n".join(escaped_ops).encode("ascii")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii") + stream_bytes + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(buf))
        buf.extend(obj)

    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(buf)


@router.get("")
def list_batches(
    batch_status: str | None = Query(default=None, alias="status"),
    line_id: int | None = Query(default=None, gt=0),
    shift: str | None = Query(default=None, max_length=40),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict[str, Any]]:
    query = scoped_query(db, ProductionBatch, user)
    if batch_status:
        norm = batch_status.strip().upper()
        if norm == "ACTIVE":
            query = query.filter(ProductionBatch.status.in_(["ACTIVE", "active"]))
        elif norm in ("CLOSED", "COMPLETED"):
            query = query.filter(ProductionBatch.status.in_(["CLOSED", "closed", "completed", "COMPLETED"]))
        elif norm == "PAUSED":
            query = query.filter(ProductionBatch.status.in_(["PAUSED", "paused"]))
        else:
            query = query.filter(ProductionBatch.status == batch_status)
    if line_id:
        query = query.filter(ProductionBatch.line_id == line_id)
    if shift:
        query = query.filter(ProductionBatch.shift == shift)
    rows = query.order_by(ProductionBatch.started_at.desc(), ProductionBatch.id.desc()).limit(200).all()
    return [_serialize_batch(db, r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_batch(
    payload: BatchCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    if scoped_query(db, ProductionBatch, user).filter(ProductionBatch.batch_code == payload.batch_code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Batch code already exists")

    line_obj: ProductionLine | None = None
    if payload.line_id:
        line_obj = scoped_one(db, ProductionLine, user, payload.line_id)
        if not line_obj:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production line not found")
    if payload.recipe_id and not scoped_one(db, InspectionRecipe, user, payload.recipe_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SKU recipe not found")

    sku_obj: ProductSKU | None = None
    if payload.sku_id is not None:
        sku_obj = scoped_one(db, ProductSKU, user, payload.sku_id)
        if sku_obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ProductSKU not found")
    elif payload.sku_code:
        sku_obj = scoped_query(db, ProductSKU, user).filter(ProductSKU.sku_code == payload.sku_code).first()

    resolved_sku_code = payload.sku_code or (sku_obj.sku_code if sku_obj else "FOOD-SKU-01")
    resolved_line_name = payload.line_name or (line_obj.name if line_obj else "Line 01 — Food Packaging")
    resolved_target = payload.target_quantity if payload.target_quantity is not None else payload.target_qty

    # Close older active batches on the same line automatically
    if payload.line_id or payload.line_name:
        active_peers = scoped_query(db, ProductionBatch, user).filter(
            ProductionBatch.status.in_(["ACTIVE", "active"]),
        )
        if payload.line_id:
            active_peers = active_peers.filter(ProductionBatch.line_id == payload.line_id)
        elif payload.line_name:
            active_peers = active_peers.filter(ProductionBatch.line_name == payload.line_name)
        for peer in active_peers.all():
            peer.status = "CLOSED"
            peer.closed_at = datetime.now(timezone.utc)

    row = ProductionBatch(
        batch_code=payload.batch_code,
        sku_id=sku_obj.id if sku_obj else payload.sku_id,
        sku_code=resolved_sku_code,
        recipe_id=payload.recipe_id,
        line_id=payload.line_id,
        line_name=resolved_line_name,
        shift=payload.shift,
        started_by=user.id,
        status="ACTIVE",
        target_quantity=resolved_target,
        total_inspected=0,
        total_pass=0,
        total_fail=0,
        total_review=0,
        defect_counts_json={
            "SEAL_DEFECT": 0,
            "MISSING_EXP_DATE": 0,
            "BURNT_OR_BROKEN": 0,
            "FOREIGN_MATTER": 0,
            "seal_leak": 0,
            "missing_exp_date": 0,
            "foreign_matter": 0,
            "burnt": 0,
        },
    )
    stamp_org(row, user)
    db.add(row)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="batch.started",
            details_json={
                "batch_code": row.batch_code,
                "sku_id": row.sku_id,
                "sku_code": row.sku_code,
                "line_name": row.line_name,
                "shift": row.shift,
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_batch(db, row)


@router.get("/{batch_id}")
def get_batch_detail(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> dict[str, Any]:
    row = _batch_or_404(db, user, batch_id)
    summary = _serialize_batch(db, row)
    reject_inspections = (
        scoped_query(db, Inspection, user)
        .filter(Inspection.batch_id == row.batch_code, Inspection.decision.in_(["FAIL", "REVIEW"]))
        .order_by(Inspection.created_at.desc())
        .limit(25)
        .all()
    )
    summary["reject_evidence"] = [
        {
            "inspection_id": ins.id,
            "decision": ins.decision,
            "defect_category": ins.defect_category or (ins.metadata_json or {}).get("defect_categories", ["FOREIGN_MATTER"])[0],
            "evidence_path": ins.evidence_path,
            "thumbnail_url": f"/inspections/{ins.id}/evidence" if ins.evidence_path else None,
            "anomaly_score": (ins.anomaly_score_json or {}).get("score"),
            "confidence": (
                max((float(d.get("confidence", 0.0)) for d in (ins.detection_results_json or {}).get("detections", []) if isinstance(d, dict)), default=0.95)
            ),
            "created_at": ins.created_at.isoformat() if ins.created_at else None,
            "defect_categories": (ins.metadata_json or {}).get("defect_categories", []),
        }
        for ins in reject_inspections
    ]
    return summary


@router.post("/{batch_id}/pause")
def pause_batch(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    row = _batch_or_404(db, user, batch_id)
    row.status = "PAUSED"
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="batch.paused",
            details_json={"batch_id": row.id, "batch_code": row.batch_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_batch(db, row)


@router.post("/{batch_id}/resume")
def resume_batch(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    row = _batch_or_404(db, user, batch_id)
    row.status = "ACTIVE"
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="batch.resumed",
            details_json={"batch_id": row.id, "batch_code": row.batch_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_batch(db, row)


@router.post("/{batch_id}/close")
@router.post("/{batch_id}/complete")
def close_batch(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    row = _batch_or_404(db, user, batch_id)
    row.status = "CLOSED"
    row.closed_at = row.closed_at or datetime.now(timezone.utc)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="batch.closed",
            details_json={"batch_id": row.id, "batch_code": row.batch_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_batch(db, row)


@router.post("/{batch_id}/sign")
def sign_batch_audit(
    batch_id: int,
    payload: BatchSignRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*QA_SIGN_ROLES)),
) -> dict[str, Any]:
    """QA Supervisor digital signature for HACCP / ISO 22000 / BPOM batch release."""
    row = _batch_or_404(db, user, batch_id)
    now_ts = datetime.now(timezone.utc)
    if row.status.upper() in ("ACTIVE", "PAUSED"):
        row.status = "quarantined" if payload.quarantine else "completed"
        row.closed_at = row.closed_at or now_ts
    elif payload.quarantine:
        row.status = "quarantined"

    canonical = json.dumps(
        {
            "org_id": row.org_id,
            "batch_code": row.batch_code,
            "sku_code": row.sku_code,
            "total_inspected": row.total_inspected,
            "total_pass": row.total_pass,
            "total_fail": row.total_fail,
            "total_review": row.total_review,
            "defects": row.defect_counts_json or {},
            "qa_user": user.username,
            "signed_at": now_ts.isoformat(),
        },
        sort_keys=True,
    )
    sig_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    row.qa_signed_by_user_id = user.id
    row.qa_signature_hash = sig_hash
    row.qa_signed_at = now_ts
    row.qa_notes = payload.qa_notes

    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="batch.qa_signed",
            details_json={
                "batch_id": row.id,
                "batch_code": row.batch_code,
                "qa_signature_hash": sig_hash,
                "status": row.status,
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_batch(db, row)


@router.get("/{batch_id}/audit-report")
@router.get("/{batch_id}/export")
def export_batch_report(
    batch_id: int,
    format: Literal["pdf", "csv"] = Query(default="pdf"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> Response:
    """Export official HACCP / ISO 22000 / BPOM Batch Audit Report as PDF or CSV."""
    detail = get_batch_detail(batch_id, db, user)
    defects = detail["defect_counts"]
    total_fail = max(1, int(detail["total_fail"]))
    std_categories = ["SEAL_DEFECT", "MISSING_EXP_DATE", "BURNT_OR_BROKEN", "FOREIGN_MATTER"]

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["HACCP / ISO 22000 / BPOM BATCH AUDIT REPORT"])
        writer.writerow(["organization_name", detail["organization_name"]])
        writer.writerow(["batch_code", detail["batch_code"]])
        writer.writerow(["sku_code", detail["sku_code"]])
        writer.writerow(["sku_name", detail["sku_name"]])
        writer.writerow(["line_name", detail["line_name"]])
        writer.writerow(["shift", detail["shift"]])
        writer.writerow(["status", detail["status"]])
        writer.writerow(["operator", detail["operator_username"] or "system"])
        writer.writerow(["started_at", detail["started_at"]])
        writer.writerow(["closed_at", detail["closed_at"] or "in_progress"])
        writer.writerow(["target_quantity", detail["target_quantity"]])
        writer.writerow(["total_inspected", detail["total_inspected"]])
        writer.writerow(["total_pass", detail["total_pass"]])
        writer.writerow(["total_fail", detail["total_fail"]])
        writer.writerow(["total_review", detail["total_review"]])
        writer.writerow(["first_pass_yield_pct", detail["first_pass_yield_pct"]])
        writer.writerow(["yield_pct", detail["yield_pct"]])
        writer.writerow(["reject_rate_pct", detail["reject_rate_pct"]])
        writer.writerow([])
        writer.writerow(["DEFECT_CATEGORY", "COUNT", "SHARE_OF_FAIL_PCT"])
        for cat in std_categories + ["seal_leak", "missing_exp_date", "burnt", "foreign_matter"]:
            cnt = int(defects.get(cat, 0))
            pct = round((cnt / total_fail) * 100.0, 1) if detail["total_fail"] > 0 else 0.0
            writer.writerow([cat, cnt, pct])
        writer.writerow([])
        writer.writerow(["QA_SUPERVISOR_SIGNATURE"])
        writer.writerow(["signed_by", detail["qa_signed_by_username"] or "UNSIGNED"])
        writer.writerow(["signature_sha256", detail["qa_signature_hash"] or "PENDING"])
        writer.writerow(["signed_at", detail["qa_signed_at"] or ""])
        writer.writerow(["qa_notes", detail["qa_notes"] or ""])
        writer.writerow([])
        writer.writerow(["REJECT_EVIDENCE_ATTACHMENTS", "DECISION", "DEFECT_CATEGORY", "CONFIDENCE_OR_SCORE", "TIMESTAMP", "EVIDENCE_PATH"])
        for ev in detail["reject_evidence"]:
            score_val = ev["anomaly_score"] if ev["anomaly_score"] is not None else ev["confidence"]
            writer.writerow([
                f"INS-{ev['inspection_id']:06d}",
                ev["decision"],
                ev["defect_category"],
                score_val,
                ev["created_at"] or "",
                ev["evidence_path"] or "sampled_out",
            ])

        filename = f"batch-audit-{detail['batch_code']}.csv"
        return Response(
            content=buf.getvalue().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # PDF export
    lines = [
        "INDUSTRIALVISION AI - FOOD DIVISION QA/QC AUDIT REPORT",
        "Compliance Standard: HACCP / ISO 22000 / FSSC 22000 / BPOM",
        "================================================================",
        f"Organization     : {detail['organization_name']}",
        f"Batch / Lot Code : {detail['batch_code']}",
        f"Product SKU      : {detail['sku_code']} ({detail['sku_name']})",
        f"Production Line  : {detail['line_name']}   |   Shift: {detail['shift']}",
        f"Operator / QA    : {detail['operator_username'] or 'line-operator'}   |   Status: {detail['status'].upper()}",
        f"Started At (UTC) : {detail['started_at']}",
        f"Closed At (UTC)  : {detail['closed_at'] or 'ACTIVE'}",
        "----------------------------------------------------------------",
        "PRODUCTION QUANTITY & FIRST PASS YIELD SUMMARY",
        f"Target Quantity  : {detail['target_quantity']}",
        f"Total Production : {detail['total_inspected']}",
        f"Total PASS       : {detail['total_pass']}  (First Pass Yield: {detail['first_pass_yield_pct']}%)",
        f"Total FAIL       : {detail['total_fail']}  (Reject Rate: {detail['reject_rate_pct']}%)",
        f"Total REVIEW     : {detail['total_review']}",
        "----------------------------------------------------------------",
        "DEFECT CATEGORY BREAKDOWN (MULTI-ZONE ROI)",
    ]
    for cat in std_categories:
        cnt = int(defects.get(cat, 0))
        pct = round((cnt / total_fail) * 100.0, 1) if detail["total_fail"] > 0 else 0.0
        lines.append(f"  - {cat:<20}: {cnt:>5} pcs  ({pct}% of rejects)")

    lines.extend(
        [
            "----------------------------------------------------------------",
            "VISUAL ATTACHMENTS: FAIL SAMPLE EVIDENCE & SCORES",
        ]
    )
    if detail["reject_evidence"]:
        for ev in detail["reject_evidence"][:10]:
            score_label = (
                f"anomaly={ev['anomaly_score']:.3f}"
                if ev["anomaly_score"] is not None
                else f"conf={ev['confidence']:.2f}"
            )
            lines.append(
                f"  * INS-{ev['inspection_id']:06d} [{ev['defect_category']}] {score_label} @ {ev['created_at']} -> {ev['evidence_path'] or 'retained'}"
            )
    else:
        lines.append("  * Zero reject evidence attachments recorded in this batch.")

    lines.extend(
        [
            "----------------------------------------------------------------",
            "QA SUPERVISOR DIGITAL SIGNATURE & VERIFICATION",
            f"Verified By      : {detail['qa_signed_by_username'] or 'PENDING QA SIGN-OFF'}",
            f"Signed At (UTC)  : {detail['qa_signed_at'] or 'N/A'}",
            f"SHA-256 Digest   : {detail['qa_signature_hash'] or 'UNSIGNED'}",
            f"QA Notes         : {(detail['qa_notes'] or 'None')[:80]}",
            "================================================================",
        ]
    )

    pdf_bytes = _render_simple_pdf(lines)
    filename = f"batch-audit-{detail['batch_code']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/retention/prune")
def trigger_evidence_retention_prune(
    payload: RetentionPruneRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*QA_SIGN_ROLES)),
) -> dict[str, Any]:
    """Execute Smart Evidence Retention pruning (7d PASS, 365d+ FAIL/REVIEW audit)."""
    result = cleanup_expired_pass_evidence(
        db,
        pass_retention_days=payload.pass_retention_days,
        audit_retention_days=payload.audit_retention_days,
    )
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="evidence.retention_pruned",
            details_json=result,
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return result
