"""Tests for Fase 3: Food-Grade Image Preprocessing & Camera Health / Lens Fog Guard.

Covers:
1. Configurable Preprocessing Pipeline (`app/vision/preprocessing.py`):
   - CLAHE on LAB Luminance channel for specular glare / low-contrast enhancement.
   - Gray-World White Balance + Gamma normalization across inter-shift lighting changes.
   - HSV Color Masking for bakery/snack bake maturity vs burnt detection.
2. Lens Quality & Factory Environment Guard (`app/vision/quality_guard.py`):
   - Sharpness / Fog Score via Laplacian Variance on Gaussian-blurred synthetic images
     -> flags `LENS_BLUR_OR_FOG`.
   - Illumination Shift detection on dead-lamp / dark images -> flags `LIGHTING_FAILURE`.
3. End-to-End Fail-Safe Alerting:
   - Blurred or dark frames NEVER produce a false PASS -> return `ERROR` with rationale
     `"Camera lens obstructed/fogged or lighting failure"`, transition `Camera.status`
     to `DEGRADED`, and emit real-time WebSocket + Prometheus metrics.
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
from app.db.models import Camera, ModelVersion, Organization, Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.event_bus import event_bus
from app.services.evidence import EvidenceStorage
from app.services.live_feed import inspection_topic
from app.vision.contracts import InferenceResult
from app.vision.preprocessing import (
    apply_clahe,
    apply_gamma_correction,
    apply_hsv_color_mask,
    apply_white_balance,
    preprocess_food_frame,
)
from app.vision.quality_guard import FAILURE_RATIONALE, evaluate_frame_quality


class StubEngine:
    def __init__(self, result: InferenceResult):
        self.result = result

    def predict(self, _image):
        return self.result


class PreprocessingAndQualityGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        db = self.session_factory()
        db.add(Organization(id=1, name="Food Plant Org", slug="food-plant"))
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        self.admin = User(username="qa_eng", hashed_password="", role_id=roles[RoleNames.AI_ENGINEER].id, is_active=True, org_id=1)
        self.admin.role_name = RoleNames.AI_ENGINEER
        db.add(self.admin)
        db.flush()

        self.cam = Camera(
            org_id=1,
            name="Flow-Wrap Cam 01",
            camera_identity="CAM-FW-01",
            source_type="upload",
            status=CameraStatus.CONNECTED,
            is_active=True,
        )
        self.model = ModelVersion(
            org_id=1,
            name="food-detector-onnx",
            framework="onnx",
            framework_version="1.16.0",
            source_path="food.onnx",
            task_type="object_detection",
            is_active=True,
            config_json={"reject_labels": ["SEAL_DEFECT"], "detection_confidence": 0.5},
        )
        db.add_all([self.cam, self.model])
        db.commit()

        self.cam_id = self.cam.id
        self.model_id = self.model.id
        self.headers = {"Authorization": f"Bearer {create_access_token(self.admin.id, RoleNames.AI_ENGINEER)}"}
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

    def test_1_clahe_glare_suppression_white_balance_and_hsv_mask(self):
        """Verify CLAHE boosts local contrast on low-contrast/glare images, White Balance stabilizes color cast, and HSV mask detects bake color."""
        # Low-contrast glossy packaging image with multi-level texture compressed in [110, 141]
        yy, xx = np.mgrid[0:160, 0:240]
        base_grad = (110 + ((xx * 3 + yy * 2) % 32)).astype(np.uint8)
        low_contrast = np.stack([base_grad, base_grad, base_grad], axis=-1)

        before_local_std = float(np.std(low_contrast))
        enhanced = apply_clahe(low_contrast, clip_limit=3.0, tile_size=8)
        after_local_std = float(np.std(enhanced))
        self.assertGreater(after_local_std, before_local_std * 1.5, "CLAHE must expand local luminance contrast in low-contrast regions")

        # Warm night-shift lighting cast (strong red/yellow dominance, weak blue)
        cast_img = np.zeros((100, 100, 3), dtype=np.uint8)
        cast_img[:, :] = (90, 150, 210)  # BGR
        balanced = apply_white_balance(cast_img)
        b_mean, g_mean, r_mean = [float(balanced[:, :, c].mean()) for c in range(3)]
        # Channel spread after white balance must be narrower than initial (210 - 90 = 120)
        self.assertLess(max(b_mean, g_mean, r_mean) - min(b_mean, g_mean, r_mean), 80.0)

        # Gamma correction check
        gamma_out = apply_gamma_correction(low_contrast, gamma=1.3)
        self.assertEqual(gamma_out.shape, low_contrast.shape)

        # HSV Color Masking: golden-brown biscuit (BGR ~ 60, 140, 210 -> HSV ~ 19, 182, 210)
        biscuit_img = np.full((80, 80, 3), (60, 140, 210), dtype=np.uint8)
        hsv_good = apply_hsv_color_mask(biscuit_img, hsv_lower=(10, 40, 50), hsv_upper=(35, 255, 245))
        self.assertGreater(hsv_good["in_range_ratio"], 0.90)
        self.assertEqual(hsv_good["burnt_ratio"], 0.0)

        # Burnt biscuit (BGR ~ 15, 18, 20 -> V <= 55)
        burnt_img = np.full((80, 80, 3), (15, 18, 20), dtype=np.uint8)
        hsv_burnt = apply_hsv_color_mask(burnt_img)
        self.assertGreater(hsv_burnt["burnt_ratio"], 0.95)

    def test_2_quality_guard_detects_gaussian_blur_and_lighting_failure(self):
        """Verify quality_guard flags LENS_BLUR_OR_FOG on Gaussian-blurred images and LIGHTING_FAILURE on dead-lamp frames."""
        sharp = np.full((240, 320, 3), 205, dtype=np.uint8)
        for x in range(15, 305, 8):
            cv2.line(sharp, (x, 10), (x, 230), (35, 35, 35), 1)
        cv2.putText(sharp, "EXP 17.11.27", (60, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (15, 15, 15), 2)

        q_sharp = evaluate_frame_quality(sharp, {"blur_threshold": 80.0})
        self.assertEqual(q_sharp["status"], "OK")
        self.assertFalse(q_sharp["is_obstructed"])
        self.assertGreater(q_sharp["sharpness_score"], 80.0)

        # Apply heavy Gaussian blur simulating steam/washdown lens fog
        fogged = cv2.GaussianBlur(sharp, (51, 51), 25.0)
        q_fog = evaluate_frame_quality(fogged, {"blur_threshold": 80.0})
        self.assertEqual(q_fog["status"], "LENS_BLUR_OR_FOG")
        self.assertTrue(q_fog["is_obstructed"])
        self.assertEqual(q_fog["decision_if_failed"], "ERROR")
        self.assertIn(FAILURE_RATIONALE, q_fog["rationale"])

        # Simulate dead inspection lamp (dark frame, mean_luminance < 15)
        dark = np.full((240, 320, 3), 5, dtype=np.uint8)
        q_dark = evaluate_frame_quality(dark, {"min_luminance": 20.0})
        self.assertEqual(q_dark["status"], "LIGHTING_FAILURE")
        self.assertTrue(q_dark["is_obstructed"])
        self.assertFalse(q_dark["lighting_ok"])
        self.assertIn(FAILURE_RATIONALE, q_dark["rationale"])

    def test_3_end_to_end_blurred_upload_sets_camera_degraded_and_emits_ws_and_prometheus(self):
        """Verify blurred image never produces false PASS, sets camera DEGRADED, and emits WS + Prometheus alert."""
        # Create active InspectionRecipe on cam_id
        rec_res = self.client.post(
            "/api/v1/recipes",
            headers=self.headers,
            json={
                "sku_code": "SNK-CRISP-50G",
                "name": "Potato Crisps 50g Recipe",
                "camera_id": self.cam_id,
                "is_active": True,
                "preprocessing_config": {
                    "clahe_enabled": True,
                    "white_balance_enabled": True,
                    "gamma": 1.2,
                    "blur_threshold": 80.0,
                },
            },
        )
        self.assertEqual(rec_res.status_code, 201, rec_res.text)

        # Build Gaussian-blurred image
        fogged = np.full((200, 300, 3), 150, dtype=np.uint8)
        cv2.circle(fogged, (150, 100), 40, (165, 165, 165), -1)
        fogged = cv2.GaussianBlur(fogged, (51, 51), 25.0)
        ok, encoded = cv2.imencode(".jpg", fogged)
        self.assertTrue(ok)

        # Subscribe to WebSocket event bus topic to assert real-time alert emission
        topic = inspection_topic(1)
        sub_queue = event_bus.subscribe(topic)
        try:
            stub_res = InferenceResult(detections=[], latency_ms=3.2)
            with patch.object(inspection_router, "create_detection_engine", return_value=StubEngine(stub_res)):
                resp = self.client.post(
                    "/inspections/image",
                    headers=self.headers,
                    data={"camera_id": str(self.cam_id), "model_version_id": str(self.model_id)},
                    files={"file": ("fogged_lens.jpg", encoded.tobytes(), "image/jpeg")},
                )
            self.assertEqual(resp.status_code, 201, resp.text)
            body = resp.json()
            # Never false PASS -> must be ERROR with the required rationale
            self.assertEqual(body["decision"], "ERROR")
            self.assertIn(FAILURE_RATIONALE, body["decision_rationale"])

            # Verify camera status transitioned to DEGRADED
            cam_out = self.client.get(f"/cameras/{self.cam_id}", headers=self.headers).json()
            self.assertEqual(cam_out["status"], CameraStatus.DEGRADED)
            self.assertIn(FAILURE_RATIONALE, cam_out["last_error"])

            # Verify WebSocket event bus received the alert
            self.assertFalse(sub_queue.empty(), "WebSocket bus must receive the quality alert / ERROR inspection event")

            # Verify Prometheus metrics endpoint exposes iv_camera_quality_guard_alerts_total
            metrics_text = self.client.get("/metrics").text
            self.assertIn("iv_camera_quality_guard_alerts_total", metrics_text)
            self.assertIn("LENS_BLUR_OR_FOG", metrics_text)
        finally:
            event_bus.unsubscribe(topic, sub_queue)


if __name__ == "__main__":
    unittest.main()
