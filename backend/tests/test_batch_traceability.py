"""Tests for Fase 2: Food Safety & HACCP Traceability (`test_batch_traceability.py`).

Covers:
1. ProductionBatch lifecycle (ACTIVE -> PAUSED -> CLOSED), atomic counter updates
   (total_inspected, total_pass, total_fail, total_review), standardized defect_category
   on Inspection, and First Pass Yield (%) calculation.
2. Smart Evidence Retention Policy (`app/services/evidence_retention.py`):
   - PASS sampling mode (1 in N stored on disk, remaining N-1 metadata-only in DB).
   - FAIL & REVIEW 100% full-resolution storage + 365-day anti-delete audit lock.
   - RQ worker task cleanup of expired PASS files without touching FAIL evidence.
3. HACCP / QA Batch Audit Export (`GET /api/v1/batches/{id}/audit-report?format=pdf|csv`):
   - Header (Org, Batch/Lot, SKU, Line, Shift, Start-Close, Operator/QA).
   - Summary stats (Total Production, First Pass Yield %, Defect Breakdown).
   - Visual attachments for FAIL samples + QA Supervisor digital signature.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import CameraStatus, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import (
    Camera,
    Inspection,
    InspectionSession,
    ModelVersion,
    Organization,
    ProductionLine,
    Role,
    User,
)
from app.db.session import get_db
from app.main import create_app
from app.services.evidence import EvidenceStorage
from app.services.evidence_retention import (
    cleanup_expired_pass_evidence,
    delete_evidence_with_audit_guard,
    persist_inspection_evidence,
)


class BatchTraceabilityPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.evidence_root = Path(self.temp_dir.name) / "evidence"
        self.evidence_patch = patch.object(settings, "EVIDENCE_ROOT", str(self.evidence_root))
        self.evidence_patch.start()
        self.addCleanup(self.evidence_patch.stop)

        db = self.session_factory()
        org = Organization(id=1, name="PT Pangan Nusantara QA", slug="pangan-nusantara")
        db.add(org)
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        self.qa_admin = User(username="qa_supervisor", hashed_password="", role_id=roles[RoleNames.ADMIN].id, is_active=True, org_id=1)
        self.qa_admin.role_name = RoleNames.ADMIN
        self.operator = User(username="operator_shift1", hashed_password="", role_id=roles[RoleNames.OPERATOR].id, is_active=True, org_id=1)
        self.operator.role_name = RoleNames.OPERATOR
        db.add_all([self.qa_admin, self.operator])
        db.flush()

        line = ProductionLine(org_id=1, name="Line 01 — Biscuit Flow-Wrap", description="High speed line")
        db.add(line)
        db.flush()

        cam = Camera(
            org_id=1,
            name="Station Cam 01",
            camera_identity="CAM-L01-01",
            source_type="simulated",
            line_id=line.id,
            status=CameraStatus.CONNECTED,
            is_active=True,
        )
        model = ModelVersion(
            org_id=1,
            name="food-multizone-onnx",
            framework="onnx",
            framework_version="1.16.0",
            source_path="food.onnx",
            task_type="object_detection",
            is_active=True,
            config_json={"reject_labels": ["SEAL_DEFECT", "MISSING_EXP_DATE", "FOREIGN_MATTER", "BURNT_OR_BROKEN"], "detection_confidence": 0.5},
        )
        db.add_all([cam, model])
        db.commit()

        self.line_id = line.id
        self.camera_id = cam.id
        self.model_id = model.id
        self.admin_headers = {"Authorization": f"Bearer {create_access_token(self.qa_admin.id, RoleNames.ADMIN)}"}
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

    def test_1_batch_lifecycle_yield_calculation_and_audit_report_pdf_csv(self):
        """Test batch creation, atomic counters, First Pass Yield (%), lifecycle, and PDF/CSV audit report."""
        sku = self.client.post(
            "/api/v1/skus",
            headers=self.admin_headers,
            json={
                "sku_code": "BIS-CHOC-100G",
                "name": "Biskuit Cokelat Premium 100g",
                "category": "bakery",
                "packaging_type": "flow_wrap",
            },
        ).json()

        batch_resp = self.client.post(
            "/api/v1/batches",
            headers=self.op_headers,
            json={
                "batch_code": "LOT-20261116-A1",
                "sku_id": sku["id"],
                "line_id": self.line_id,
                "line_name": "Line 01 — Biscuit Flow-Wrap",
                "shift": "SHIFT_1",
                "target_quantity": 500,
            },
        )
        self.assertEqual(batch_resp.status_code, 201, batch_resp.text)
        batch = batch_resp.json()
        batch_id = batch["id"]
        self.assertEqual(batch["status"], "ACTIVE")
        self.assertEqual(batch["shift"], "SHIFT_1")
        self.assertEqual(batch["target_quantity"], 500)

        # Run 8 PASS inspections and 2 FAIL inspections (1 seal_leak, 1 missing_exp_date)
        for _ in range(8):
            r = self.client.post(
                f"/ot/trigger/{self.camera_id}",
                headers=self.op_headers,
                json={"protocol": "simulated"},
            )
            self.assertEqual(r.status_code, 201)

        f_seal = self.client.post(
            f"/ot/trigger/{self.camera_id}",
            headers=self.op_headers,
            json={"protocol": "simulated", "simulate_defect": "seal_leak"},
        )
        self.assertEqual(f_seal.status_code, 201)
        self.assertEqual(f_seal.json()["defect_category"], "SEAL_DEFECT")

        f_exp = self.client.post(
            f"/ot/trigger/{self.camera_id}",
            headers=self.op_headers,
            json={"protocol": "simulated", "simulate_defect": "missing_exp_date"},
        )
        self.assertEqual(f_exp.status_code, 201)
        self.assertEqual(f_exp.json()["defect_category"], "MISSING_EXP_DATE")

        # Check batch counters and First Pass Yield (8 / 10 = 80.0%)
        detail = self.client.get(f"/api/v1/batches/{batch_id}", headers=self.op_headers).json()
        self.assertEqual(detail["total_inspected"], 10)
        self.assertEqual(detail["total_pass"], 8)
        self.assertEqual(detail["total_fail"], 2)
        self.assertEqual(detail["first_pass_yield_pct"], 80.0)
        self.assertEqual(detail["defect_counts"]["SEAL_DEFECT"], 1)
        self.assertEqual(detail["defect_counts"]["MISSING_EXP_DATE"], 1)
        self.assertEqual(len(detail["reject_evidence"]), 2)

        # Test Pause -> Resume -> Close lifecycle
        paused = self.client.post(f"/api/v1/batches/{batch_id}/pause", headers=self.op_headers)
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["status"], "PAUSED")

        resumed = self.client.post(f"/api/v1/batches/{batch_id}/resume", headers=self.op_headers)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["status"], "ACTIVE")

        closed = self.client.post(f"/api/v1/batches/{batch_id}/close", headers=self.op_headers)
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["status"], "CLOSED")
        self.assertIsNotNone(closed.json()["closed_at"])

        # QA Supervisor signs batch
        signed = self.client.post(
            f"/api/v1/batches/{batch_id}/sign",
            headers=self.admin_headers,
            json={"qa_notes": "HACCP CCP-1 verified & released by QA Supervisor"},
        )
        self.assertEqual(signed.status_code, 200)
        sig_hash = signed.json()["qa_signature_hash"]
        self.assertIsNotNone(sig_hash)

        # Download PDF Audit Report via /api/v1/batches/{id}/audit-report?format=pdf
        pdf_res = self.client.get(f"/api/v1/batches/{batch_id}/audit-report?format=pdf", headers=self.op_headers)
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res.headers["content-type"], "application/pdf")
        self.assertTrue(pdf_res.content.startswith(b"%PDF-1.4"))
        self.assertIn(b"PT Pangan Nusantara QA", pdf_res.content)
        self.assertIn(b"LOT-20261116-A1", pdf_res.content)
        self.assertIn(b"First Pass Yield: 80.0%", pdf_res.content)
        self.assertIn(b"SEAL_DEFECT", pdf_res.content)
        self.assertIn(sig_hash.encode("ascii"), pdf_res.content)

        # Download CSV Audit Report via /api/v1/batches/{id}/audit-report?format=csv
        csv_res = self.client.get(f"/api/v1/batches/{batch_id}/audit-report?format=csv", headers=self.op_headers)
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn("PT Pangan Nusantara QA", csv_res.text)
        self.assertIn("LOT-20261116-A1", csv_res.text)
        self.assertIn("first_pass_yield_pct,80.0", csv_res.text)
        self.assertIn("SEAL_DEFECT,1", csv_res.text)
        self.assertIn("MISSING_EXP_DATE,1", csv_res.text)

    def test_2_smart_evidence_retention_sampling_and_anti_delete_protection(self):
        """Test 1-in-N PASS sampling vs 100% FAIL retention and 365-day anti-delete guard."""
        storage = EvidenceStorage(self.evidence_root)
        sample_rate = 5

        saved_pass_paths = []
        for seq in range(1, 13):
            path, meta = persist_inspection_evidence(
                storage,
                inspection_id=100 + seq,
                decision="PASS",
                sequence_index=seq,
                content=b"pass-jpeg-bytes",
                extension=".jpg",
                pass_sample_rate=sample_rate,
            )
            if seq in (1, 6, 11):
                self.assertIsNotNone(path, f"PASS seq {seq} must be sampled (1 in {sample_rate})")
                self.assertTrue(meta["evidence_sampled"])
                saved_pass_paths.append(path)
            else:
                self.assertIsNone(path, f"PASS seq {seq} must skip disk write")
                self.assertFalse(meta["evidence_sampled"])

        self.assertEqual(len(saved_pass_paths), 3)

        # FAIL & REVIEW are 100% stored and audit-protected
        db = self.session_factory()
        try:
            now = datetime.now(timezone.utc)
            sess = InspectionSession(org_id=1, camera_id=self.camera_id, model_version_id=self.model_id)
            db.add(sess)
            db.flush()

            fail_ins = Inspection(
                org_id=1,
                session_id=sess.id,
                camera_id=self.camera_id,
                decision="FAIL",
                defect_category="SEAL_DEFECT",
                created_at=now - timedelta(days=30),
            )
            expired_pass_ins = Inspection(
                org_id=1,
                session_id=sess.id,
                camera_id=self.camera_id,
                decision="PASS",
                created_at=now - timedelta(days=14),
            )
            db.add_all([fail_ins, expired_pass_ins])
            db.flush()

            fail_path, fail_meta = persist_inspection_evidence(
                storage,
                inspection_id=fail_ins.id,
                decision="FAIL",
                sequence_index=2,
                content=b"fail-audit-bytes",
                extension=".jpg",
                pass_sample_rate=100,
                audit_retention_days=365,
            )
            pass_path, _ = persist_inspection_evidence(
                storage,
                inspection_id=expired_pass_ins.id,
                decision="PASS",
                sequence_index=1,
                content=b"old-pass-bytes",
                extension=".jpg",
                pass_sample_rate=100,
            )
            fail_ins.evidence_path = fail_path
            expired_pass_ins.evidence_path = pass_path
            db.commit()

            self.assertTrue(fail_meta["audit_protected"])
            # Anti-delete guard refuses deleting FAIL evidence within 365-day window
            with self.assertRaises(PermissionError):
                delete_evidence_with_audit_guard(fail_ins, storage, audit_retention_days=365, now=now)
            self.assertTrue(storage.resolve(fail_ins.evidence_path).is_file())

            # Background cleanup removes expired PASS evidence (>7 days) without touching FAIL evidence
            prune_res = cleanup_expired_pass_evidence(db, storage, pass_retention_days=7, audit_retention_days=365, now=now)
            self.assertEqual(prune_res["pruned_pass"], 1)
            self.assertEqual(prune_res["pruned_audit"], 0)
            self.assertGreaterEqual(prune_res["protected_fail_preserved"], 1)

            db.refresh(expired_pass_ins)
            db.refresh(fail_ins)
            self.assertIsNone(expired_pass_ins.evidence_path)
            self.assertIsNotNone(fail_ins.evidence_path)
            self.assertTrue(storage.resolve(fail_ins.evidence_path).is_file())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
