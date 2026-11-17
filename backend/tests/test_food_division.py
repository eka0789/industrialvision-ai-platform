"""Comprehensive tests for the 6 Food Division Enhancements:
1. OT Bridge (Modbus TCP / MQTT / Simulated trigger & PLC rejector actuation)
2. SKU Recipe & Multi-Zone ROI (Seal PatchCore, Expiry OCR, Product HSV+YOLO, Changeover)
3. Food Safety Traceability (ProductionBatch, QA Digital Signature, PDF/CSV Export, Smart Evidence Retention)
4. Washdown / Glare Preprocessing (CLAHE, Gamma, Laplacian Variance BLUR_ALERT -> DEGRADED)
5. Food Packaging PdM Sensors (sealer_temp_c, pneumatic_pressure_bar) & Vision-PdM Correlation
6. RAG Food Division SOPs (HACCP / Washdown / Packaging) & 5-Min Defect Spike Agent + HITL Approval
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import CameraStatus, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import (
    AgentApproval,
    Camera,
    Inspection,
    InspectionSession,
    Machine,
    ModelVersion,
    Organization,
    ProductionLine,
    Role,
    SystemEvent,
    User,
)
from app.db.session import get_db
from app.main import create_app
from app.services.evidence import EvidenceStorage, prune_expired_evidence
from app.vision.preprocessing import compute_laplacian_variance, preprocess_food_frame


class FoodDivisionEnhancementsTest(unittest.TestCase):
    def setUp(self):
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

        self.owner = User(username="qa-supervisor", hashed_password="", role_id=roles[RoleNames.OWNER].id, is_active=True, org_id=1)
        self.owner.role_name = RoleNames.OWNER
        self.operator = User(username="line-op", hashed_password="", role_id=roles[RoleNames.OPERATOR].id, is_active=True, org_id=1)
        self.operator.role_name = RoleNames.OPERATOR
        db.add_all([self.owner, self.operator])
        db.flush()

        line = ProductionLine(org_id=1, name="Line 01 — Biscuit Flow-Wrap", description="High-speed 120 ppm packaging")
        db.add(line)
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
        db.commit()

        self.line_id = line.id
        self.camera_id = cam.id
        self.model_id = model.id
        self.owner_headers = {"Authorization": f"Bearer {create_access_token(self.owner.id, RoleNames.OWNER)}"}
        self.op_headers = {"Authorization": f"Bearer {create_access_token(self.operator.id, RoleNames.OPERATOR)}"}
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def override_db(self):
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def test_1_sku_recipes_multizone_roi_and_plc_changeover(self):
        """Enhancement 2: Create SKU recipes with 3-Zone ROI and switch active SKU via changeover."""
        r1 = self.client.post(
            "/recipes",
            headers=self.owner_headers,
            json={
                "sku_code": "BIS-COK-100G",
                "name": "Biskuit Cokelat 100g",
                "line_id": self.line_id,
                "camera_id": self.camera_id,
                "model_version_id": self.model_id,
                "is_active": True,
                "pass_sample_rate": 500,
            },
        )
        self.assertEqual(r1.status_code, 201, r1.text)
        body1 = r1.json()
        self.assertTrue(body1["is_active"])
        self.assertEqual(len(body1["zones_json"]), 3)
        zone_types = [z["zone_type"] for z in body1["zones_json"]]
        self.assertEqual(zone_types, ["seal_integrity", "expiry_code", "product_quality"])

        # Create second variant (Keju 100g)
        r2 = self.client.post(
            "/recipes",
            headers=self.owner_headers,
            json={
                "sku_code": "BIS-KEJ-100G",
                "name": "Biskuit Keju 100g",
                "line_id": self.line_id,
                "camera_id": self.camera_id,
                "model_version_id": self.model_id,
                "is_active": False,
            },
        )
        self.assertEqual(r2.status_code, 201)

        # Trigger PLC/MES SKU changeover to BIS-KEJ-100G by operator
        changeover = self.client.post(
            "/recipes/changeover",
            headers=self.op_headers,
            json={"sku_code": "BIS-KEJ-100G", "camera_id": self.camera_id, "source": "plc_mes"},
        )
        self.assertEqual(changeover.status_code, 200)
        self.assertTrue(changeover.json()["is_active"])

        # Verify BIS-COK-100G was automatically deactivated on that camera
        active_list = self.client.get(f"/recipes?camera_id={self.camera_id}&active_only=true", headers=self.op_headers).json()
        self.assertEqual(len(active_list), 1)
        self.assertEqual(active_list[0]["sku_code"], "BIS-KEJ-100G")

    def test_2_ot_bridge_trigger_and_batch_traceability_pdf_csv_export(self):
        """Enhancements 1, 3, 4: Start Batch -> OT Triggers (PASS, FAIL, BLUR_ALERT) -> QA Sign -> PDF/CSV Export."""
        # Create active recipe & start production batch
        self.client.post(
            "/recipes",
            headers=self.owner_headers,
            json={
                "sku_code": "BIS-COK-100G",
                "name": "Biskuit Cokelat 100g",
                "line_id": self.line_id,
                "camera_id": self.camera_id,
                "is_active": True,
                "pass_sample_rate": 500,
            },
        )
        batch_res = self.client.post(
            "/batches",
            headers=self.op_headers,
            json={
                "batch_code": "BATCH-20261117-S1-001",
                "sku_code": "BIS-COK-100G",
                "line_id": self.line_id,
                "shift": "Shift 1",
                "target_qty": 2000,
            },
        )
        self.assertEqual(batch_res.status_code, 201, batch_res.text)
        batch_id = batch_res.json()["id"]

        # 1) Trigger PASS #1 (sampled because seq=1)
        t_pass1 = self.client.post(
            f"/ot/trigger/{self.camera_id}",
            headers=self.op_headers,
            json={"sensor_id": "PHOTO-01", "protocol": "modbus_tcp", "actuator_type": "pneumatic_air_blast"},
        )
        self.assertEqual(t_pass1.status_code, 201, t_pass1.text)
        p1 = t_pass1.json()
        self.assertEqual(p1["decision"], "PASS")
        self.assertFalse(p1["rejector"]["actuated"])
        self.assertTrue(p1["evidence_saved"], "First PASS in batch must be sampled")

        # 2) Trigger PASS #2 (skipped disk write by 1-in-500 smart retention!)
        t_pass2 = self.client.post(
            f"/ot/trigger/{self.camera_id}",
            headers=self.op_headers,
            json={"sensor_id": "PHOTO-01", "protocol": "modbus_tcp"},
        )
        self.assertEqual(t_pass2.status_code, 201)
        p2 = t_pass2.json()
        self.assertEqual(p2["decision"], "PASS")
        self.assertFalse(p2["evidence_saved"], "Second PASS must skip disk write under 1-in-500 retention policy")

        # 3) Trigger FAIL (seal_leak) -> PLC rejector actuated + 100% evidence saved
        t_fail = self.client.post(
            f"/ot/trigger/{self.camera_id}",
            headers=self.op_headers,
            json={
                "sensor_id": "PHOTO-01",
                "protocol": "modbus_tcp",
                "actuator_type": "diverter_arm",
                "simulate_defect": "seal_leak",
            },
        )
        self.assertEqual(t_fail.status_code, 201)
        f1 = t_fail.json()
        self.assertEqual(f1["decision"], "FAIL")
        self.assertIn("seal_leak", f1["defect_categories"])
        self.assertTrue(f1["rejector"]["actuated"])
        self.assertEqual(f1["rejector"]["actuator_type"], "diverter_arm")
        self.assertTrue(f1["evidence_saved"], "FAIL must save 100% full-res audit evidence")

        # 4) Trigger Washdown Steam Blur -> Camera becomes DEGRADED (BLUR_ALERT) and decision is ERROR
        t_blur = self.client.post(
            f"/ot/trigger/{self.camera_id}",
            headers=self.op_headers,
            json={"sensor_id": "PHOTO-01", "protocol": "mqtt", "simulate_blur": True},
        )
        self.assertEqual(t_blur.status_code, 201)
        b1 = t_blur.json()
        self.assertEqual(b1["decision"], "ERROR")
        self.assertEqual(b1["camera_status"], CameraStatus.DEGRADED)
        self.assertTrue(b1["preprocessing"]["is_blurred"])
        self.assertIn("BLUR_ALERT", b1["reason"])

        # Verify SystemEvent for camera.blur_alert was logged
        db = self.session_factory()
        try:
            blur_ev = db.query(SystemEvent).filter(SystemEvent.event_type == "camera.blur_alert").first()
            self.assertIsNotNone(blur_ev)
        finally:
            db.close()

        # 5) QA Supervisor signs batch
        sign_res = self.client.post(
            f"/batches/{batch_id}/sign",
            headers=self.owner_headers,
            json={"qa_notes": "Batch verified compliant with HACCP CCP-1 & BPOM"},
        )
        self.assertEqual(sign_res.status_code, 200)
        signed = sign_res.json()
        self.assertEqual(signed["status"], "completed")
        self.assertIsNotNone(signed["qa_signature_hash"])
        self.assertEqual(signed["total_pass"], 2)
        self.assertEqual(signed["total_fail"], 1)
        self.assertEqual(signed["defect_counts"]["seal_leak"], 1)

        # 6) Export PDF & CSV Batch Audit Reports
        pdf_res = self.client.get(f"/batches/{batch_id}/export?format=pdf", headers=self.op_headers)
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res.headers["content-type"], "application/pdf")
        self.assertTrue(pdf_res.content.startswith(b"%PDF-1.4"))
        self.assertIn(b"BATCH-20261117-S1-001", pdf_res.content)
        self.assertIn(signed["qa_signature_hash"].encode("ascii"), pdf_res.content)

        csv_res = self.client.get(f"/batches/{batch_id}/export?format=csv", headers=self.op_headers)
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn("HACCP / ISO 22000 / BPOM BATCH AUDIT REPORT", csv_res.text)
        self.assertIn("seal_leak,1", csv_res.text)

    def test_3_tiered_evidence_retention_pruning(self):
        """Enhancement 3: 7-day PASS pruning vs 730-day FAIL/REVIEW audit preservation."""
        db = self.session_factory()
        storage = EvidenceStorage()
        try:
            now = datetime.now(timezone.utc)
            sess = InspectionSession(org_id=1, camera_id=self.camera_id, model_version_id=self.model_id)
            db.add(sess)
            db.flush()

            # Old PASS (10 days old -> should be pruned under 7-day rule)
            old_pass = Inspection(
                org_id=1,
                session_id=sess.id,
                camera_id=self.camera_id,
                decision="PASS",
                created_at=now - timedelta(days=10),
            )
            # Old FAIL (300 days old -> MUST BE KEPT under 730-day 2-year audit rule!)
            audit_fail = Inspection(
                org_id=1,
                session_id=sess.id,
                camera_id=self.camera_id,
                decision="FAIL",
                created_at=now - timedelta(days=300),
            )
            db.add_all([old_pass, audit_fail])
            db.flush()

            old_pass.evidence_path = storage.save_image(old_pass.id, b"pass-img-bytes", ".jpg")
            audit_fail.evidence_path = storage.save_image(audit_fail.id, b"fail-audit-bytes", ".jpg")
            db.commit()

            summary = prune_expired_evidence(db, storage, pass_retention_days=7, audit_retention_days=730)
            self.assertEqual(summary["pruned_pass"], 1)
            self.assertEqual(summary["pruned_audit"], 0)

            db.refresh(old_pass)
            db.refresh(audit_fail)
            self.assertIsNone(old_pass.evidence_path)
            self.assertIsNotNone(audit_fail.evidence_path)
            self.assertTrue(storage.resolve(audit_fail.evidence_path).is_file())
        finally:
            db.close()

    def test_4_food_pdm_sensors_and_vision_correlation_plus_rag_agent_hitl(self):
        """Enhancements 5 & 6: Heat sealer temp drop + 5-min seal_leak spike -> RAG SOP + HITL Approval."""
        # 1) Create food packaging machine linked to camera
        mac_res = self.client.post(
            "/pdm/machines",
            headers=self.op_headers,
            json={
                "name": "Horizontal Flow-Wrap Sealer L01",
                "code": "FW-SEAL-01",
                "machine_type": "food_packaging",
                "camera_id": self.camera_id,
            },
        )
        self.assertEqual(mac_res.status_code, 201)
        machine_id = mac_res.json()["id"]

        # 2) Record low heat sealer temperature (< 140 C) & low pneumatic pressure (< 5.5 bar)
        read_res = self.client.post(
            f"/pdm/machines/{machine_id}/readings",
            headers=self.op_headers,
            json={
                "sealer_temp_c": 128.0,
                "pneumatic_pressure_bar": 4.3,
                "zone_temp_c": 185.0,
                "conveyor_speed_mpm": 120.0,
                "motor_current_a": 8.5,
            },
        )
        self.assertEqual(read_res.status_code, 201, read_res.text)
        self.assertEqual(read_res.json()["food_features"]["sealer_temp_c"], 128.0)

        # 3) Trigger 3 consecutive seal_leak FAILs within 5 minutes
        for _ in range(3):
            self.client.post(
                f"/ot/trigger/{self.camera_id}",
                headers=self.op_headers,
                json={"simulate_defect": "seal_leak"},
            )

        # 4) Verify PdM prediction flags SEAL_TEMP_LOW & PNEUMATIC_DROP and confirms Vision correlation
        pred_res = self.client.post(f"/pdm/machines/{machine_id}/predict", headers=self.op_headers)
        self.assertEqual(pred_res.status_code, 200)
        pred = pred_res.json()
        self.assertIn("SEAL_TEMP_LOW", pred["failure_modes"])
        self.assertIn("PNEUMATIC_DROP", pred["failure_modes"])
        self.assertTrue(pred["vision_correlation"]["correlated"])
        self.assertGreaterEqual(pred["vision_correlation"]["seal_leak_count"], 3)

        # 5) Seed Food Division SOPs into RAG
        sop_res = self.client.post("/rag/seed-food-sops", headers=self.op_headers)
        self.assertEqual(sop_res.status_code, 201, sop_res.text)
        self.assertGreaterEqual(len(sop_res.json()["documents"]), 3)

        # 6) Evaluate 5-minute defect spike via Operations Agent -> retrieves SOP & creates HITL approval
        spike_res = self.client.post(
            "/agent/evaluate-defect-spike",
            headers=self.op_headers,
            json={
                "defect_category": "seal_leak",
                "window_minutes": 5,
                "spike_threshold": 3,
                "camera_id": self.camera_id,
                "auto_propose_approval": True,
            },
        )
        self.assertEqual(spike_res.status_code, 200, spike_res.text)
        spike = spike_res.json()
        self.assertTrue(spike["spike_detected"])
        self.assertGreaterEqual(spike["matched_fail_count"], 3)
        self.assertTrue(len(spike["sop_citations"]) > 0)
        self.assertIsNotNone(spike["approval_id"])

        # 7) QA Supervisor (owner) approves the HITL corrective action -> creates MaintenanceRequest
        approval_id = spike["approval_id"]
        approve_res = self.client.post(
            f"/agent/approvals/{approval_id}/approve",
            headers=self.owner_headers,
            json={"note": "Approved per HACCP SOP-QA-001 — calibrate sealer bar immediately"},
        )
        self.assertEqual(approve_res.status_code, 200, approve_res.text)
        self.assertEqual(approve_res.json()["status"], "executed")


if __name__ == "__main__":
    unittest.main()
