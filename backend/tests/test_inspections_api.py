"""Upload, persistence, pagination, and error-path API tests with mocked models."""

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

from app.core.config import settings
from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import Camera, Inspection, ModelVersion, Role, User
from app.db.session import get_db
from app.main import create_app
from app.schemas.inspection import InspectionOut
from app.services.evidence import EvidenceStorage
from app.vision.contracts import DetectionPrediction, InferenceResult
from app.api.v1.routers.inspections import inspection as inspection_router


class StubEngine:
    def __init__(self, result):
        self.result = result

    def predict(self, _image):
        return self.result


class InspectionApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()
        operator_role = Role(name=RoleNames.OPERATOR, description="Operator")
        db.add(operator_role)
        db.flush()
        user = User(username="qc-operator", hashed_password="", role_id=operator_role.id, is_active=True)
        user.set_password("operator-password-123")
        camera = Camera(name="Upload station", source_type="upload", is_active=True)
        model = ModelVersion(
            name="fixture-model",
            framework="onnx",
            framework_version="test-v1",
            source_path="fixture.onnx",
            config_json={"reject_labels": ["scratch"], "confidence_threshold": 0.5},
        )
        db.add_all([user, camera, model])
        db.commit()
        self.user_id = user.id
        self.camera_id = camera.id
        self.model_id = model.id
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)
        self.headers = {
            "Authorization": f"Bearer {create_access_token(self.user_id, RoleNames.OPERATOR)}"
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = EvidenceStorage(Path(self.temp_dir.name) / "evidence")
        self.storage_patch = patch.object(inspection_router, "EvidenceStorage", lambda: self.storage)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

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

    @staticmethod
    def jpeg_bytes():
        ok, encoded = cv2.imencode(".jpg", np.zeros((40, 60, 3), dtype=np.uint8))
        if not ok:
            raise RuntimeError("Test JPEG encoding failed")
        return encoded.tobytes()

    def post_image(self, filename="../../unsafe.jpg", content_type="image/jpeg"):
        return self.client.post(
            "/inspections/image",
            headers=self.headers,
            data={
                "camera_id": str(self.camera_id),
                "model_version_id": str(self.model_id),
                "product_id": "product-001",
                "batch_id": "batch-001",
            },
            files={"file": (filename, self.jpeg_bytes(), content_type)},
        )

    def test_image_pass_result_and_evidence_are_persisted(self):
        result = InferenceResult(detections=[], latency_ms=4.2)
        with patch.object(inspection_router, "create_detection_engine", return_value=StubEngine(result)):
            response = self.post_image()

        self.assertEqual(response.status_code, 201, response.text)
        payload = InspectionOut.model_validate(response.json())
        self.assertEqual(payload.decision, "PASS")
        self.assertIn("passed", payload.decision_reason)
        self.assertEqual(payload.batch_id, "batch-001")
        evidence = self.storage.resolve(payload.evidence_path)
        self.assertTrue(evidence.is_file())
        self.assertEqual(evidence.read_bytes(), self.jpeg_bytes())

        response = self.client.get(f"/inspections/{payload.id}/evidence", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.jpeg_bytes())
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.client.get(f"/inspections/{payload.id}/evidence").status_code, 401)
        self.assertEqual(self.client.get("/inspections/99999/evidence", headers=self.headers).status_code, 404)

    def test_defect_result_and_detection_rows_are_persisted(self):
        result = InferenceResult(
            detections=[DetectionPrediction(label="scratch", confidence=0.91, bbox=(1, 2, 8, 9))],
            latency_ms=5.5,
        )
        with patch.object(inspection_router, "create_detection_engine", return_value=StubEngine(result)):
            response = self.post_image()

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "FAIL")
        self.assertEqual(payload["detections"][0]["bbox"], {"x1": 1.0, "y1": 2.0, "x2": 8.0, "y2": 9.0})

    def test_model_load_failure_is_persisted_as_error(self):
        with patch.object(inspection_router, "create_detection_engine", side_effect=FileNotFoundError("weights missing")):
            response = self.post_image()

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "ERROR")
        self.assertEqual(payload["error_status"], "inference_failed")
        db = self.session_factory()
        try:
            stored = db.get(Inspection, payload["id"])
            self.assertEqual(stored.decision, "ERROR")
            self.assertEqual(stored.error_message, "weights missing")
        finally:
            db.close()

    def test_invalid_upload_is_rejected_before_creating_inspection(self):
        response = self.client.post(
            "/inspections/image",
            headers=self.headers,
            data={"camera_id": str(self.camera_id), "model_version_id": str(self.model_id)},
            files={"file": ("fake.jpg", b"not an image", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 415)
        db = self.session_factory()
        try:
            self.assertEqual(db.query(Inspection).count(), 0)
        finally:
            db.close()

    def test_pixel_limit_is_checked_before_decoding(self):
        with patch.object(settings, "MAX_IMAGE_PIXELS", 10):
            response = self.post_image()
        self.assertEqual(response.status_code, 413)
        db = self.session_factory()
        try:
            self.assertEqual(db.query(Inspection).count(), 0)
        finally:
            db.close()

    def test_upload_requires_authentication(self):
        response = self.client.post(
            "/inspections/image",
            data={"camera_id": str(self.camera_id), "model_version_id": str(self.model_id)},
            files={"file": ("photo.jpg", self.jpeg_bytes(), "image/jpeg")},
        )
        self.assertEqual(response.status_code, 401)

    def test_history_filters_and_detail_are_database_backed(self):
        result = InferenceResult(detections=[], latency_ms=2.0)
        with patch.object(inspection_router, "create_detection_engine", return_value=StubEngine(result)):
            first = self.post_image()
            second = self.post_image()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

        page = self.client.get(
            "/inspections?page=1&page_size=1&decision=PASS&camera_id=" + str(self.camera_id),
            headers=self.headers,
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["total"], 2)
        self.assertTrue(page.json()["has_more"])
        inspection_id = page.json()["items"][0]["id"]
        detail = self.client.get(f"/inspections/{inspection_id}", headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["id"], inspection_id)
        self.assertEqual(self.client.get("/inspections/99999", headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
