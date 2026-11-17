"""Unit & integration tests for Fase 1: SKU Recipe Management & Multi-Zone ROI Inspection.

Covers:
1. Multi-zone cropping (x, y, width, height in 0.0-1.0) and task_type evaluation
   (anomaly_segmentation, ocr_expiry, object_detection).
2. Decision aggregation (PASS/FAIL/REVIEW) with zone rationale in decision_rationale & detections,
   including end-to-end /inspections/image with an active camera recipe.
3. Atomic activation (only 1 active InspectionRecipe per camera_id) + AuditLog recording.
4. Multi-tenant org_id isolation across /api/v1/skus and /api/v1/recipes.
5. RBAC enforcement (admin & ai_engineer write SKU/Recipe; operator can read & activate changeover; viewer read-only).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.routers.inspections import inspection as inspection_router
from app.core.constants import CameraStatus, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import AuditLog, Camera, ModelVersion, Organization, Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.evidence import EvidenceStorage
from app.vision.contracts import InferenceResult
from app.vision.roi_pipeline import ROIZoneSpec, crop_roi, evaluate_recipe_frame


class StubDetectionEngine:
    def __init__(self, result: InferenceResult):
        self.result = result

    def predict(self, _image):
        return self.result


class SkuRecipesPhase1Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        db = self.session_factory()
        db.add_all([
            Organization(id=1, name="Food Division Org A", slug="food-org-a"),
            Organization(id=2, name="Food Division Org B", slug="food-org-b"),
        ])
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        def make_user(username: str, role_name: str, org_id: int) -> User:
            u = User(username=username, hashed_password="", role_id=roles[role_name].id, is_active=True, org_id=org_id)
            u.set_password("Password!2026")
            u.role_name = role_name
            db.add(u)
            return u

        self.admin_a = make_user("admin_a", RoleNames.ADMIN, 1)
        self.engineer_a = make_user("eng_a", RoleNames.AI_ENGINEER, 1)
        self.operator_a = make_user("op_a", RoleNames.OPERATOR, 1)
        self.viewer_a = make_user("view_a", RoleNames.VIEWER, 1)
        self.admin_b = make_user("admin_b", RoleNames.ADMIN, 2)
        db.flush()

        self.cam1 = Camera(org_id=1, name="Line 1 Cam A", source_type="upload", status=CameraStatus.CONNECTED, is_active=True)
        self.cam2 = Camera(org_id=1, name="Line 1 Cam B", source_type="upload", status=CameraStatus.CONNECTED, is_active=True)
        self.cam_b = Camera(org_id=2, name="Org B Cam", source_type="upload", status=CameraStatus.CONNECTED, is_active=True)
        self.model = ModelVersion(
            org_id=1,
            name="food-yolov8n",
            framework="onnx",
            framework_version="1.16.0",
            source_path="food.onnx",
            task_type="object_detection",
            is_active=True,
            config_json={"reject_labels": ["FOREIGN_MATTER", "SEAL_DEFECT"], "detection_confidence": 0.5},
        )
        db.add_all([self.cam1, self.cam2, self.cam_b, self.model])
        db.commit()

        self.cam1_id = self.cam1.id
        self.cam2_id = self.cam2.id
        self.model_id = self.model.id

        self.headers = {
            "admin_a": {"Authorization": f"Bearer {create_access_token(self.admin_a.id, RoleNames.ADMIN)}"},
            "eng_a": {"Authorization": f"Bearer {create_access_token(self.engineer_a.id, RoleNames.AI_ENGINEER)}"},
            "op_a": {"Authorization": f"Bearer {create_access_token(self.operator_a.id, RoleNames.OPERATOR)}"},
            "view_a": {"Authorization": f"Bearer {create_access_token(self.viewer_a.id, RoleNames.VIEWER)}"},
            "admin_b": {"Authorization": f"Bearer {create_access_token(self.admin_b.id, RoleNames.ADMIN)}"},
        }
        db.close()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = EvidenceStorage(Path(self.temp_dir.name) / "evidence")
        self.storage_patch = patch.object(inspection_router, "EvidenceStorage", lambda: self.storage)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

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

    def test_1_multi_zone_cropping_and_decision_aggregation_unit(self):
        """Verify normalized (x, y, width, height) cropping and zone decision aggregation."""
        img = np.full((200, 400, 3), 210, dtype=np.uint8)
        # Add crisp grid lines so Laplacian variance > 80 (not blurred)
        for x in range(10, 390, 10):
            cv2.line(img, (x, 5), (x, 195), (60, 60, 60), 1)

        zones = [
            {
                "zone_id": "z_seal",
                "zone_name": "Top Heat Seal",
                "x": 0.10,
                "y": 0.05,
                "width": 0.80,
                "height": 0.20,
                "task_type": "anomaly_segmentation",
                "model_id": self.model_id,
                "confidence_threshold": 0.60,
                "defect_category": "SEAL_DEFECT",
            },
            {
                "zone_id": "z_ocr",
                "zone_name": "Expiry Code Print",
                "x": 0.50,
                "y": 0.30,
                "width": 0.40,
                "height": 0.20,
                "task_type": "ocr_expiry",
                "model_id": None,
                "confidence_threshold": 0.55,
                "defect_category": "MISSING_EXP_DATE",
            },
            {
                "zone_id": "z_prod",
                "zone_name": "Biscuit Window",
                "x": 0.15,
                "y": 0.55,
                "width": 0.70,
                "height": 0.40,
                "task_type": "object_detection",
                "model_id": self.model_id,
                "confidence_threshold": 0.50,
                "defect_category": "BURNT_OR_BROKEN",
            },
        ]

        spec0 = ROIZoneSpec.model_validate(zones[0])
        crop0, px_bbox0 = crop_roi(img, spec0)
        self.assertEqual(px_bbox0, (40, 10, 360, 50))
        self.assertEqual(crop0.shape[:2], (40, 320))

        # Case A: All zones pass
        pass_eval = evaluate_recipe_frame(
            img,
            zones_data=zones,
            override_seal_score=0.18,
            simulated_ocr_text="EXP 24.12.27",
        )
        self.assertEqual(pass_eval["decision"], "PASS")
        self.assertIn("passed", pass_eval["decision_rationale"].lower())

        # Case B: OCR zone fails -> overall FAIL with zone rationale & MISSING_EXP_DATE detection
        fail_eval = evaluate_recipe_frame(
            img,
            zones_data=zones,
            override_seal_score=0.18,
            simulated_ocr_text="",
        )
        self.assertEqual(fail_eval["decision"], "FAIL")
        self.assertIn("Expiry Code Print", fail_eval["decision_rationale"])
        self.assertIn("MISSING_EXP_DATE", fail_eval["decision_rationale"])
        self.assertTrue(any(d["label"] == "MISSING_EXP_DATE" for d in fail_eval["detections"]))

    def test_2_rbac_and_atomic_activation_with_audit_log(self):
        """Verify RBAC rules and atomic 1-active-recipe-per-camera changeover."""
        # 1) Operator cannot create SKU (403)
        op_sku = self.client.post(
            "/api/v1/skus",
            headers=self.headers["op_a"],
            json={"sku_code": "SNK-001", "name": "Potato Crisps", "category": "snack", "packaging_type": "flow_wrap"},
        )
        self.assertEqual(op_sku.status_code, 403)

        # 2) AI Engineer creates SKU_1 and Admin creates SKU_2 (201)
        sku1 = self.client.post(
            "/api/v1/skus",
            headers=self.headers["eng_a"],
            json={
                "sku_code": "BAK-CHOC-100",
                "name": "Choco Croissant 100g",
                "category": "bakery",
                "packaging_type": "flow_wrap",
            },
        )
        self.assertEqual(sku1.status_code, 201, sku1.text)
        sku1_id = sku1.json()["id"]

        sku2 = self.client.post(
            "/api/v1/skus",
            headers=self.headers["admin_a"],
            json={
                "sku_code": "DRY-MILK-250",
                "name": "Fresh Milk 250ml",
                "category": "dairy",
                "packaging_type": "carton",
            },
        )
        self.assertEqual(sku2.status_code, 201)
        sku2_id = sku2.json()["id"]

        # 3) Operator cannot create Recipe (403), AI Engineer can (201)
        zone_payload = [
            {
                "zone_id": "seal_top",
                "zone_name": "Top Seal Area",
                "x": 0.05,
                "y": 0.02,
                "width": 0.90,
                "height": 0.18,
                "task_type": "anomaly_segmentation",
                "model_id": self.model_id,
                "confidence_threshold": 0.58,
                "defect_category": "SEAL_DEFECT",
            }
        ]
        op_rec = self.client.post(
            "/api/v1/recipes",
            headers=self.headers["op_a"],
            json={"sku_id": sku1_id, "camera_id": self.cam1_id, "name": "Recipe Op", "roi_zones": zone_payload},
        )
        self.assertEqual(op_rec.status_code, 403)

        rec1 = self.client.post(
            "/api/v1/recipes",
            headers=self.headers["eng_a"],
            json={
                "sku_id": sku1_id,
                "camera_id": self.cam1_id,
                "name": "Croissant Cam1 Recipe",
                "is_active": True,
                "preprocessing_config": {"clahe_enabled": True, "gamma": 1.25, "blur_threshold": 80.0},
                "roi_zones": zone_payload,
            },
        )
        self.assertEqual(rec1.status_code, 201, rec1.text)
        rec1_id = rec1.json()["id"]
        self.assertTrue(rec1.json()["is_active"])

        rec2 = self.client.post(
            "/api/v1/recipes",
            headers=self.headers["admin_a"],
            json={
                "sku_id": sku2_id,
                "camera_id": self.cam1_id,
                "name": "Milk Cam1 Recipe",
                "is_active": False,
                "roi_zones": zone_payload,
            },
        )
        self.assertEqual(rec2.status_code, 201)
        rec2_id = rec2.json()["id"]

        # 4) Viewer cannot activate recipe (403), Operator CAN activate recipe during changeover (200)
        view_act = self.client.post(f"/api/v1/recipes/{rec2_id}/activate", headers=self.headers["view_a"])
        self.assertEqual(view_act.status_code, 403)

        op_act = self.client.post(f"/api/v1/recipes/{rec2_id}/activate", headers=self.headers["op_a"])
        self.assertEqual(op_act.status_code, 200, op_act.text)
        self.assertTrue(op_act.json()["is_active"])

        # 5) Verify atomic deactivation of rec1 on cam1 & AuditLog entry
        r1_after = self.client.get(f"/api/v1/recipes/{rec1_id}", headers=self.headers["op_a"]).json()
        self.assertFalse(r1_after["is_active"], "Previous recipe on the same camera must be atomically deactivated")

        db = self.session_factory()
        try:
            audit = (
                db.query(AuditLog)
                .filter(AuditLog.action == "recipe.activated", AuditLog.user_id == self.operator_a.id)
                .first()
            )
            self.assertIsNotNone(audit)
            self.assertEqual(audit.details_json["recipe_id"], rec2_id)
        finally:
            db.close()

    def test_3_multi_tenant_isolation_and_image_inspection_pipeline(self):
        """Verify org_id isolation and /inspections/image executing active recipe ROI zones."""
        # Create SKU and active recipe in Org 1 with forced OCR expiry failure
        sku_a = self.client.post(
            "/api/v1/skus",
            headers=self.headers["admin_a"],
            json={"sku_code": "BEV-COLA-330", "name": "Cola Can 330ml", "category": "beverage", "packaging_type": "can"},
        ).json()

        # Org B can create identical sku_code without conflict and cannot see Org A's SKUs
        sku_b_res = self.client.post(
            "/api/v1/skus",
            headers=self.headers["admin_b"],
            json={"sku_code": "BEV-COLA-330", "name": "Org B Cola", "category": "beverage", "packaging_type": "can"},
        )
        self.assertEqual(sku_b_res.status_code, 201)
        self.assertEqual(self.client.get(f"/api/v1/skus/{sku_a['id']}", headers=self.headers["admin_b"]).status_code, 404)

        rec_a = self.client.post(
            "/api/v1/recipes",
            headers=self.headers["admin_a"],
            json={
                "sku_id": sku_a["id"],
                "camera_id": self.cam1_id,
                "name": "Cola Expiry Check Recipe",
                "is_active": True,
                "roi_zones": [
                    {
                        "zone_id": "exp_zone",
                        "zone_name": "Can Bottom Expiry",
                        "x": 0.2,
                        "y": 0.2,
                        "width": 0.6,
                        "height": 0.6,
                        "task_type": "ocr_expiry",
                        "confidence_threshold": 0.6,
                        "defect_category": "MISSING_EXP_DATE",
                    }
                ],
            },
        ).json()

        # Cross-tenant recipe access returns 404
        self.assertEqual(
            self.client.post(f"/api/v1/recipes/{rec_a['id']}/activate", headers=self.headers["admin_b"]).status_code,
            404,
        )

        # Upload crisp image to /inspections/image on cam1 -> executes active recipe ROI pipeline
        img = np.full((120, 160, 3), 200, dtype=np.uint8)
        for x in range(10, 150, 8):
            cv2.line(img, (x, 5), (x, 115), (40, 40, 40), 1)
        ok, encoded = cv2.imencode(".jpg", img)
        self.assertTrue(ok)

        stub_res = InferenceResult(detections=[], latency_ms=5.1)
        with patch.object(inspection_router, "create_detection_engine", return_value=StubDetectionEngine(stub_res)):
            resp = self.client.post(
                "/inspections/image",
                headers=self.headers["op_a"],
                data={"camera_id": str(self.cam1_id), "model_version_id": str(self.model_id)},
                files={"file": ("can.jpg", encoded.tobytes(), "image/jpeg")},
            )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["decision"], "PASS")
        self.assertIsNotNone(body["decision_rationale"])
        self.assertEqual(body["metadata"]["recipe_id"], rec_a["id"])


if __name__ == "__main__":
    unittest.main()
