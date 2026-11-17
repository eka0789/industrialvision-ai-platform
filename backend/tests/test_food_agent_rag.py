"""Phase 6 verification tests: Food Safety RAG SOP Seed, Bounded Agent Tools, RBAC, and HITL Approval."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.tools import ToolContext, ToolRefusal, get_tool
from app.core.config import settings
from app.core.constants import CameraStatus, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import (
    Camera,
    InspectionRecipe,
    ModelVersion,
    Organization,
    ProductionBatch,
    ProductionLine,
    ProductSKU,
    Role,
    User,
)
from app.db.session import get_db
from app.main import create_app
from app.rag.food_sop_seed import FOOD_SOP_SEED_DOCUMENTS, seed_food_safety_sops


class FoodAgentRagTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.evidence_patch = patch.object(settings, "EVIDENCE_ROOT", str(Path(self.temp_dir.name) / "evidence"))
        self.evidence_patch.start()
        self.addCleanup(self.evidence_patch.stop)

        db = self.session_factory()
        org = Organization(id=1, name="Food Division Plant", slug="food-plant")
        db.add(org)
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        self.owner = User(username="qa-owner", hashed_password="", role_id=roles[RoleNames.OWNER].id, is_active=True, org_id=1)
        self.owner.role_name = RoleNames.OWNER
        self.admin = User(username="qa-admin", hashed_password="", role_id=roles[RoleNames.ADMIN].id, is_active=True, org_id=1)
        self.admin.role_name = RoleNames.ADMIN
        self.operator = User(username="line-op", hashed_password="", role_id=roles[RoleNames.OPERATOR].id, is_active=True, org_id=1)
        self.operator.role_name = RoleNames.OPERATOR
        self.ai_eng = User(username="ml-eng", hashed_password="", role_id=roles[RoleNames.AI_ENGINEER].id, is_active=True, org_id=1)
        self.ai_eng.role_name = RoleNames.AI_ENGINEER
        self.viewer = User(username="auditor-viewer", hashed_password="", role_id=roles[RoleNames.VIEWER].id, is_active=True, org_id=1)
        self.viewer.role_name = RoleNames.VIEWER
        db.add_all([self.owner, self.admin, self.operator, self.ai_eng, self.viewer])
        db.flush()

        line = ProductionLine(org_id=1, name="Line 01 — Biscuit Flow-Wrap", description="High-speed 120 ppm packaging")
        sku = ProductSKU(org_id=1, sku_code="BIS-COK-100G", name="Biskuit Cokelat 100g", category="bakery", packaging_type="flow_wrap")
        db.add_all([line, sku])
        db.flush()

        cam = Camera(
            org_id=1,
            name="Biscuit Flow-Wrap Cam",
            camera_identity="CAM-FOOD-01",
            source_type="simulated",
            line_id=line.id,
            status=CameraStatus.CONNECTED,
            is_active=True,
        )
        model = ModelVersion(
            org_id=1,
            name="food-multizone-yolov8n",
            framework="onnx",
            framework_version="1.16.0",
            source_path="food.onnx",
            task_type="object_detection",
            is_active=True,
            config_json={"reject_labels": ["seal_leak", "missing_exp_date", "foreign_matter", "burnt"], "detection_confidence": 0.5},
        )
        db.add_all([cam, model])
        db.flush()

        recipe = InspectionRecipe(
            org_id=1,
            sku_id=sku.id,
            sku_code=sku.sku_code,
            name="Resep Biskuit Cokelat 3-Zona",
            camera_id=cam.id,
            model_version_id=model.id,
            is_active=True,
            preprocessing_json={"clahe_enabled": True, "gamma": 1.15, "blur_threshold": 80.0},
            zones_json=[
                {
                    "zone_id": "roi_1_seal",
                    "zone_name": "Seal Area",
                    "x": 0.05,
                    "y": 0.02,
                    "width": 0.9,
                    "height": 0.2,
                    "task_type": "anomaly_segmentation",
                    "confidence_threshold": 0.55,
                    "defect_category": "SEAL_DEFECT",
                }
            ],
        )
        batch = ProductionBatch(
            org_id=1,
            batch_code="LOT-20261117-F6",
            sku_id=sku.id,
            sku_code=sku.sku_code,
            line_id=line.id,
            line_name=line.name,
            shift="SHIFT_1",
            status="ACTIVE",
            target_quantity=2000,
            total_inspected=120,
            total_pass=115,
            total_fail=5,
            total_review=0,
        )
        db.add_all([recipe, batch])
        db.commit()

        self.camera_id = cam.id
        self.recipe_id = recipe.id
        self.batch_id = batch.id
        self.owner_headers = {"Authorization": f"Bearer {create_access_token(self.owner.id, RoleNames.OWNER)}"}
        self.admin_headers = {"Authorization": f"Bearer {create_access_token(self.admin.id, RoleNames.ADMIN)}"}
        self.op_headers = {"Authorization": f"Bearer {create_access_token(self.operator.id, RoleNames.OPERATOR)}"}
        self.ai_headers = {"Authorization": f"Bearer {create_access_token(self.ai_eng.id, RoleNames.AI_ENGINEER)}"}
        self.viewer_headers = {"Authorization": f"Bearer {create_access_token(self.viewer.id, RoleNames.VIEWER)}"}
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        self.temp_dir.cleanup()
        self.engine.dispose()

    def override_db(self):
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def test_1_bundled_food_sop_seed_and_1_to_1_citation_enforcement(self) -> None:
        """Verify SOP-QA-01, SOP-QA-02, SOP-SAN-03, SOP-FOD-04 ingestion and 1:1 citation retrieval."""
        codes = [doc["code"] for doc in FOOD_SOP_SEED_DOCUMENTS]
        self.assertEqual(codes, ["SOP-QA-01", "SOP-QA-02", "SOP-SAN-03", "SOP-FOD-04"])

        seed_res = self.client.post("/rag/seed-food-sops", headers=self.op_headers)
        self.assertEqual(seed_res.status_code, 201, seed_res.text)
        docs = seed_res.json()["documents"]
        self.assertEqual(len(docs), 4)

        # Query RAG for Foreign Matter quarantine protocol (SOP-FOD-04)
        chat_res = self.client.post(
            "/rag/chat",
            headers=self.op_headers,
            json={"question": "Bagaimana protokol karantina batch SOP-FOD-04 saat terdeteksi kontaminasi benda asing FOREIGN_MATTER?"},
        )
        self.assertEqual(chat_res.status_code, 200, chat_res.text)
        data = chat_res.json()
        self.assertEqual(data["status"], "answered")
        self.assertIn("[1]", data["answer"])
        self.assertGreaterEqual(len(data["citations"]), 1)
        self.assertEqual(data["citations"][0]["index"], 1)

    def test_2_rbac_and_hitl_approval_for_draft_batch_hold_and_recipe_threshold(self) -> None:
        """Verify RBAC restriction on draft_batch_hold_notice and HITL approval for HOLD & threshold adjustment."""
        db = self.session_factory()
        try:
            hold_tool = get_tool("draft_batch_hold_notice")
            viewer_ctx = ToolContext(org_id=1, user_id=self.viewer.id, user_role=RoleNames.VIEWER)
            with self.assertRaises(ToolRefusal):
                hold_tool.execute(
                    db,
                    viewer_ctx,
                    {"batch_id": self.batch_id, "reason": "Viewer mencoba menahan batch", "affected_qty": 50},
                )

            # Operator can draft a Batch Hold notice -> returns approval_required
            op_ctx = ToolContext(org_id=1, user_id=self.operator.id, user_role=RoleNames.OPERATOR)
            draft_res = hold_tool.execute(
                db,
                op_ctx,
                {
                    "batch_id": self.batch_id,
                    "reason": "Ditemukan kontaminasi benda asing (FOREIGN_MATTER) pada 2 kemasan biskuit",
                    "affected_qty": 240,
                },
            )
            self.assertEqual(draft_res["status"], "approval_required")
            approval_id = draft_res["approval_id"]

            # Batch status MUST still be ACTIVE before approval
            batch = db.get(ProductionBatch, self.batch_id)
            self.assertEqual(batch.status, "ACTIVE")
        finally:
            db.close()

        # AI Engineer is NOT allowed to approve draft_batch_hold_notice (requires admin or owner)
        forbidden_approve = self.client.post(
            f"/agent/approvals/{approval_id}/approve",
            headers=self.ai_headers,
            json={"note": "AI engineer trying to approve batch hold"},
        )
        self.assertEqual(forbidden_approve.status_code, 403)

        # Admin approves draft_batch_hold_notice -> Batch status changes to HOLD
        admin_approve = self.client.post(
            f"/agent/approvals/{approval_id}/approve",
            headers=self.admin_headers,
            json={"note": "Disetujui QA Manager sesuai SOP-FOD-04 — karantina lot segera"},
        )
        self.assertEqual(admin_approve.status_code, 200, admin_approve.text)
        self.assertEqual(admin_approve.json()["status"], "executed")
        self.assertEqual(admin_approve.json()["result"]["new_status"], "HOLD")

        # Propose recipe threshold adjustment by operator -> approved by AI Engineer
        db = self.session_factory()
        try:
            thr_tool = get_tool("propose_recipe_threshold_adjustment")
            op_ctx = ToolContext(org_id=1, user_id=self.operator.id, user_role=RoleNames.OPERATOR)
            prop_res = thr_tool.execute(
                db,
                op_ctx,
                {
                    "recipe_id": self.recipe_id,
                    "zone_id": "roi_1_seal",
                    "new_threshold": 0.64,
                    "justification": "Kompensasi pergeseran pencahayaan shift malam & PSI drift 0.23",
                },
            )
            self.assertEqual(prop_res["status"], "approval_required")
            thr_approval_id = prop_res["approval_id"]
        finally:
            db.close()

        ai_approve = self.client.post(
            f"/agent/approvals/{thr_approval_id}/approve",
            headers=self.ai_headers,
            json={"note": "Disetujui AI Engineer setelah evaluasi kurva ROC"},
        )
        self.assertEqual(ai_approve.status_code, 200, ai_approve.text)
        self.assertEqual(ai_approve.json()["status"], "executed")
        self.assertEqual(ai_approve.json()["result"]["new_threshold"], 0.64)

    def test_3_proactive_incident_triage_endpoint(self) -> None:
        """Verify POST /agent/triage-incident retrieves SOP citations, line quality summary, and mints HITL approval."""
        triage_res = self.client.post(
            "/agent/triage-incident",
            headers=self.op_headers,
            json={
                "incident_type": "FOREIGN_MATTER",
                "batch_id": self.batch_id,
                "affected_qty": 180,
            },
        )
        self.assertEqual(triage_res.status_code, 200, triage_res.text)
        body = triage_res.json()
        self.assertEqual(body["status"], "approval_required")
        self.assertEqual(body["incident_type"], "FOREIGN_MATTER")
        self.assertGreaterEqual(len(body["sop_citations"]), 1)
        self.assertEqual(body["line_quality_summary"]["batch_id"], self.batch_id)
        self.assertIsNotNone(body["approval_id"])


if __name__ == "__main__":
    unittest.main()
