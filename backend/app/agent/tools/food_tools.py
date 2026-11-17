"""Food Division Bounded Tools for the Operations Agent (Fase 6: `app/agent/tools/food_tools.py`).

Implements 3 org-scoped, RBAC-enforced tools with Human-in-the-Loop (HITL) approval:
1. `get_line_quality_summary(batch_id)`: Real-time yield, defect category breakdown,
   and camera lens sharpness / drift metrics for a production batch.
2. `draft_batch_hold_notice(batch_id, reason, affected_qty)`: Drafts a Batch Hold /
   quarantine notice for critical defects (`FOREIGN_MATTER`, consecutive `MISSING_EXP_DATE`).
   Requires `admin` or `owner` HITL approval before changing batch status to `HOLD`.
3. `propose_recipe_threshold_adjustment(recipe_id, zone_id, new_threshold, justification)`:
   Proposes ROI zone threshold adjustment based on data drift or lighting shifts.
   Requires `ai_engineer`, `admin`, or `owner` HITL approval before updating recipe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.constants import Role as RoleNames
from app.db.models import (
    AgentApproval,
    Camera,
    Inspection,
    InspectionRecipe,
    ProductionBatch,
)


class GetLineQualitySummaryArgs(BaseModel):
    batch_id: int | None = Field(default=None, gt=0)


class DraftBatchHoldNoticeArgs(BaseModel):
    batch_id: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=1000)
    affected_qty: int = Field(default=100, ge=1, le=1_000_000)


class ProposeRecipeThresholdAdjustmentArgs(BaseModel):
    recipe_id: int = Field(gt=0)
    zone_id: str = Field(min_length=1, max_length=120)
    new_threshold: float = Field(ge=0.05, le=0.99)
    justification: str = Field(min_length=3, max_length=1000)


TOOL_APPROVER_ROLES: dict[str, tuple[str, ...]] = {
    "create_maintenance_request": (RoleNames.OWNER, RoleNames.ADMIN),
    "draft_batch_hold_notice": (RoleNames.OWNER, RoleNames.ADMIN),
    "propose_recipe_threshold_adjustment": (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER),
}


def register_food_tools(tool_decorator, approval_executors: dict, approval_ttl_minutes: int) -> None:
    """Register the 3 Fase 6 Food Division tools and their HITL executors."""

    @tool_decorator(
        "get_line_quality_summary",
        "Get real-time batch First Pass Yield (FPY), defect trends per category, and camera lens/drift metrics.",
        GetLineQualitySummaryArgs,
        allowed_roles=RoleNames.all(),
    )
    def get_line_quality_summary(db: Session, context, args: GetLineQualitySummaryArgs) -> dict[str, Any]:
        batch_query = db.query(ProductionBatch).filter(ProductionBatch.org_id == context.org_id)
        if args.batch_id is not None:
            batch = batch_query.filter(ProductionBatch.id == args.batch_id).first()
            if batch is None:
                return {"status": "not_found", "error": f"batch {args.batch_id} not found in this organization"}
        else:
            batch = (
                batch_query.filter(ProductionBatch.status.in_(("ACTIVE", "active", "PAUSED", "paused", "HOLD")))
                .order_by(ProductionBatch.id.desc())
                .first()
                or batch_query.order_by(ProductionBatch.id.desc()).first()
            )

        ins_query = db.query(Inspection).filter(Inspection.org_id == context.org_id)
        if batch is not None:
            batch_ins = ins_query.filter(Inspection.batch_id == batch.id).order_by(Inspection.id.desc()).limit(500).all()
            if not batch_ins:
                batch_ins = ins_query.order_by(Inspection.id.desc()).limit(100).all()
        else:
            batch_ins = ins_query.order_by(Inspection.id.desc()).limit(100).all()

        defect_trends: dict[str, int] = {
            "SEAL_DEFECT": 0,
            "MISSING_EXP_DATE": 0,
            "BURNT_OR_BROKEN": 0,
            "FOREIGN_MATTER": 0,
            "LENS_BLUR_OR_FOG": 0,
        }
        sharpness_scores: list[float] = []
        anomaly_scores: list[float] = []

        for row in batch_ins:
            cat = (row.defect_category or "").upper()
            if cat:
                defect_trends[cat] = defect_trends.get(cat, 0) + 1
            for meta_cat in (row.metadata_json or {}).get("defect_categories", []):
                mc = str(meta_cat).upper()
                if mc == "SEAL_LEAK":
                    mc = "SEAL_DEFECT"
                defect_trends[mc] = defect_trends.get(mc, 0) + 1
            pre = (row.metadata_json or {}).get("preprocessing") or {}
            lap = pre.get("laplacian_variance") or pre.get("sharpness_score")
            if lap is not None:
                try:
                    sharpness_scores.append(float(lap))
                except (TypeError, ValueError):
                    pass
            if row.anomaly_score_json and row.anomaly_score_json.get("score") is not None:
                try:
                    anomaly_scores.append(float(row.anomaly_score_json["score"]))
                except (TypeError, ValueError):
                    pass

        cameras = db.query(Camera).filter(Camera.org_id == context.org_id).all()
        degraded_cameras = [c.name for c in cameras if (c.status or "").lower() == "degraded"]
        avg_sharpness = round(sum(sharpness_scores) / len(sharpness_scores), 2) if sharpness_scores else None
        # Estimate lightweight PSI-like drift indicator from anomaly score spread
        psi_drift = round(max(0.0, (sum(anomaly_scores) / len(anomaly_scores)) - 0.25), 3) if anomaly_scores else 0.04

        if batch is not None:
            total = int(batch.total_inspected or 0)
            passed = int(batch.total_pass or 0)
            failed = int(batch.total_fail or 0)
            review = int(batch.total_review or 0)
            fpy_pct = round((passed / total) * 100.0, 2) if total > 0 else 100.0
        else:
            total = len(batch_ins)
            passed = sum(1 for r in batch_ins if r.decision == "PASS")
            failed = sum(1 for r in batch_ins if r.decision == "FAIL")
            review = sum(1 for r in batch_ins if r.decision == "REVIEW")
            fpy_pct = round((passed / total) * 100.0, 2) if total > 0 else 100.0

        return {
            "status": "ok",
            "batch_id": batch.id if batch else None,
            "batch_code": batch.batch_code if batch else None,
            "batch_status": batch.status if batch else "NO_ACTIVE_BATCH",
            "sku_code": batch.sku_code if batch else None,
            "total_inspected": total,
            "total_pass": passed,
            "total_fail": failed,
            "total_review": review,
            "first_pass_yield_pct": fpy_pct,
            "defect_trends": defect_trends,
            "camera_health": {
                "avg_laplacian_sharpness": avg_sharpness,
                "degraded_cameras": degraded_cameras,
                "lens_status": "DEGRADED" if degraded_cameras else "OK",
                "psi_drift_score": psi_drift,
                "drift_alert": psi_drift >= 0.20,
            },
        }

    def _do_draft_batch_hold_notice(db: Session, org_id: int, executor_user_id: int, args_data: dict[str, Any]) -> dict[str, Any]:
        """Direct executor — runs ONLY after admin/owner approval."""
        args = DraftBatchHoldNoticeArgs.model_validate(args_data)
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.org_id == org_id, ProductionBatch.id == args.batch_id)
            .first()
        )
        if batch is None:
            return {"status": "not_found", "error": f"batch {args.batch_id} not found in this organization"}
        previous_status = batch.status
        batch.status = "HOLD"
        note_line = (
            f"[BATCH HOLD APPROVED by user #{executor_user_id}] "
            f"affected_qty={args.affected_qty} reason={args.reason}"
        )
        batch.qa_notes = f"{batch.qa_notes}\n{note_line}".strip() if batch.qa_notes else note_line
        db.commit()
        db.refresh(batch)
        return {
            "status": "ok",
            "batch_id": batch.id,
            "batch_code": batch.batch_code,
            "previous_status": previous_status,
            "new_status": batch.status,
            "affected_qty": args.affected_qty,
            "reason": args.reason,
            "approved_by_user_id": executor_user_id,
        }

    approval_executors["draft_batch_hold_notice"] = _do_draft_batch_hold_notice

    @tool_decorator(
        "draft_batch_hold_notice",
        "Draft a Batch Hold / Quarantine notice for critical food safety defects (FOREIGN_MATTER, consecutive MISSING_EXP_DATE) — REQUIRES admin or owner HITL approval before batch status changes to HOLD.",
        DraftBatchHoldNoticeArgs,
        allowed_roles=(RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER),
    )
    def draft_batch_hold_notice(db: Session, context, args: DraftBatchHoldNoticeArgs) -> dict[str, Any]:
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.org_id == context.org_id, ProductionBatch.id == args.batch_id)
            .first()
        )
        if batch is None:
            return {"status": "not_found", "error": f"batch {args.batch_id} not found in this organization"}

        title = f"[HACCP BATCH HOLD] Karantina Lot {batch.batch_code} ({args.affected_qty} pcs) — {args.reason}"[:200]
        approval_payload = {
            **args.model_dump(),
            "batch_code": batch.batch_code,
            "title": title,
        }
        approval = AgentApproval(
            org_id=context.org_id,
            tool_name="draft_batch_hold_notice",
            args_json=approval_payload,
            requested_by_user_id=context.user_id,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=approval_ttl_minutes),
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return {
            "status": "approval_required",
            "approval_id": approval.id,
            "batch_id": batch.id,
            "batch_code": batch.batch_code,
            "current_batch_status": batch.status,
            "affected_qty": args.affected_qty,
            "reason": args.reason,
            "required_approver_roles": ["admin", "owner"],
            "draft_notice": (
                f"DRAF KARANTINA BATCH (SOP-FOD-04 / SOP-QA-02): Lot {batch.batch_code} (SKU {batch.sku_code}) "
                f"sebanyak {args.affected_qty} unit diusulkan masuk status HOLD karena '{args.reason}'. "
                "Menunggu persetujuan HITL dari Admin atau Owner."
            ),
        }

    def _do_propose_recipe_threshold_adjustment(
        db: Session, org_id: int, executor_user_id: int, args_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Direct executor — runs ONLY after ai_engineer/admin/owner approval."""
        args = ProposeRecipeThresholdAdjustmentArgs.model_validate(args_data)
        recipe = (
            db.query(InspectionRecipe)
            .filter(InspectionRecipe.org_id == org_id, InspectionRecipe.id == args.recipe_id)
            .first()
        )
        if recipe is None:
            return {"status": "not_found", "error": f"recipe {args.recipe_id} not found in this organization"}

        zones = list(recipe.zones_json or [])
        updated_zone = None
        old_threshold = None
        for z in zones:
            if not isinstance(z, dict):
                continue
            zid = str(z.get("zone_id") or z.get("name") or "")
            if zid == args.zone_id or args.zone_id == "default":
                old_threshold = z.get("confidence_threshold", z.get("anomaly_threshold"))
                z["confidence_threshold"] = round(float(args.new_threshold), 4)
                if "anomaly_threshold" in z:
                    z["anomaly_threshold"] = round(float(args.new_threshold), 4)
                if "detection_confidence" in z:
                    z["detection_confidence"] = round(float(args.new_threshold), 4)
                updated_zone = zid
                break

        if updated_zone is None and zones and isinstance(zones[0], dict):
            old_threshold = zones[0].get("confidence_threshold")
            zones[0]["confidence_threshold"] = round(float(args.new_threshold), 4)
            updated_zone = str(zones[0].get("zone_id") or "zone_0")

        recipe.roi_zones = zones
        flag_modified(recipe, "roi_zones")
        db.commit()
        db.refresh(recipe)
        return {
            "status": "ok",
            "recipe_id": recipe.id,
            "recipe_name": recipe.name,
            "zone_id": updated_zone or args.zone_id,
            "old_threshold": old_threshold,
            "new_threshold": round(float(args.new_threshold), 4),
            "justification": args.justification,
            "approved_by_user_id": executor_user_id,
        }

    approval_executors["propose_recipe_threshold_adjustment"] = _do_propose_recipe_threshold_adjustment

    @tool_decorator(
        "propose_recipe_threshold_adjustment",
        "Propose an ROI zone detection/anomaly threshold adjustment due to data drift or illumination shift — REQUIRES ai_engineer, admin, or owner HITL approval.",
        ProposeRecipeThresholdAdjustmentArgs,
        allowed_roles=(RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER, RoleNames.OPERATOR),
    )
    def propose_recipe_threshold_adjustment(
        db: Session, context, args: ProposeRecipeThresholdAdjustmentArgs
    ) -> dict[str, Any]:
        recipe = (
            db.query(InspectionRecipe)
            .filter(InspectionRecipe.org_id == context.org_id, InspectionRecipe.id == args.recipe_id)
            .first()
        )
        if recipe is None:
            return {"status": "not_found", "error": f"recipe {args.recipe_id} not found in this organization"}

        zones = list(recipe.zones_json or [])
        old_threshold = None
        for z in zones:
            if isinstance(z, dict) and str(z.get("zone_id") or "") == args.zone_id:
                old_threshold = z.get("confidence_threshold", z.get("anomaly_threshold"))
                break
        if old_threshold is None and zones and isinstance(zones[0], dict):
            old_threshold = zones[0].get("confidence_threshold", 0.5)

        title = (
            f"[DRIFT THRESHOLD ADJUSTMENT] Recipe '{recipe.name}' zone '{args.zone_id}' -> {args.new_threshold:.2f}"
        )[:200]
        approval_payload = {
            **args.model_dump(),
            "recipe_name": recipe.name,
            "old_threshold": old_threshold,
            "title": title,
        }
        approval = AgentApproval(
            org_id=context.org_id,
            tool_name="propose_recipe_threshold_adjustment",
            args_json=approval_payload,
            requested_by_user_id=context.user_id,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=approval_ttl_minutes),
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return {
            "status": "approval_required",
            "approval_id": approval.id,
            "recipe_id": recipe.id,
            "recipe_name": recipe.name,
            "zone_id": args.zone_id,
            "old_threshold": old_threshold,
            "new_threshold": args.new_threshold,
            "justification": args.justification,
            "required_approver_roles": ["ai_engineer", "admin", "owner"],
            "note": (
                f"Usulan penyesuaian threshold zona '{args.zone_id}' pada resep '{recipe.name}' "
                f"dari {old_threshold} menjadi {args.new_threshold} menunggu persetujuan AI Engineer / Admin."
            ),
        }
