"""Persistence and decision integration tests without a camera or model file."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Inspection, InspectionSession, ModelVersion
from app.services import inspection_manager


class InspectionManagerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.session_factory()
        camera = Camera(name="Test camera", source_type="simulated")
        model = ModelVersion(
            name="test-detector",
            framework="test",
            framework_version="1",
            config_json={"reject_labels": ["scratch"], "detection_confidence": 0.5},
        )
        self.db.add_all([camera, model])
        self.db.flush()
        session = InspectionSession(camera_id=camera.id, model_version_id=model.id)
        self.db.add(session)
        self.db.flush()
        self.inspection = Inspection(
            session_id=session.id,
            camera_id=camera.id,
            model_name=model.name,
            model_version=model.framework_version,
            decision="ERROR",
        )
        self.db.add(self.inspection)
        self.db.commit()
        self.inspection_id = self.inspection.id
        self.db.close()
        self.manager_patch = patch.object(inspection_manager, "SessionLocal", self.session_factory)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_real_reject_detection_persists_fail(self):
        with patch.object(
            inspection_manager,
            "predict_frame",
            return_value={
                "detections": [{"label": "scratch", "confidence": 0.9, "bbox": [1, 2, 3, 4]}],
                "latency_ms": 12.5,
            },
        ):
            result = self._run_frame()

        self.assertEqual(result["decision"], "FAIL")
        db = self.session_factory()
        try:
            saved = db.get(Inspection, self.inspection_id)
            self.assertEqual(saved.decision, "FAIL")
            self.assertEqual(saved.latency_ms, 12.5)
            self.assertEqual(saved.detections[0].bbox_json["y2"], 4.0)
            self.assertIsNone(saved.anomaly)
        finally:
            db.close()

    def test_inference_exception_is_persisted_as_error(self):
        with patch.object(inspection_manager, "predict_frame", side_effect=RuntimeError("model unavailable")):
            result = self._run_frame()

        self.assertEqual(result["decision"], "ERROR")
        db = self.session_factory()
        try:
            saved = db.get(Inspection, self.inspection_id)
            self.assertEqual(saved.decision, "ERROR")
            self.assertEqual(saved.error_status, "inference_failed")
            self.assertEqual(saved.error_message, "model unavailable")
        finally:
            db.close()

    def test_configured_clean_inspection_persists_pass(self):
        with patch.object(
            inspection_manager,
            "predict_frame",
            return_value={"detections": [], "latency_ms": 3.25},
        ):
            result = self._run_frame()

        self.assertEqual(result["decision"], "PASS")
        db = self.session_factory()
        try:
            saved = db.get(Inspection, self.inspection_id)
            self.assertEqual(saved.decision, "PASS")
            self.assertIsNone(saved.error_status)
        finally:
            db.close()

    def test_missing_decision_policy_is_persisted_as_review(self):
        db = self.session_factory()
        model = db.query(ModelVersion).one()
        model.config_json = {}
        db.commit()
        db.close()

        with patch.object(
            inspection_manager,
            "predict_frame",
            return_value={"detections": [], "latency_ms": 1.0},
        ):
            result = self._run_frame()

        self.assertEqual(result["decision"], "REVIEW")

    def test_runtime_metrics_record_inference_latency(self):
        from app.services.runtime_metrics import reset_runtime_metrics

        metrics = reset_runtime_metrics()
        with patch.object(
            inspection_manager,
            "predict_frame",
            return_value={"detections": [], "latency_ms": 7.5},
        ):
            result = self._run_frame()

        self.assertEqual(result["decision"], "PASS")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["latency"]["inference"]["count"], 1)
        self.assertEqual(snapshot["latency"]["inference"]["last"], 7.5)
        self.assertEqual(snapshot["counters"]["inference_completed"], 1)
        self.assertEqual(snapshot["counters"]["inference_errors"], 0)

    def test_runtime_metrics_record_inference_errors(self):
        from app.services.runtime_metrics import reset_runtime_metrics

        metrics = reset_runtime_metrics()
        with patch.object(
            inspection_manager,
            "predict_frame",
            side_effect=RuntimeError("model unavailable"),
        ):
            result = self._run_frame()

        self.assertEqual(result["decision"], "ERROR")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["counters"]["inference_errors"], 1)
        self.assertEqual(snapshot["counters"]["inference_completed"], 0)
        self.assertEqual(snapshot["rates"]["inference_error_rate"], 1.0)
        self.assertIn("model unavailable", snapshot["recent_errors"][-1]["message"])

    def _run_frame(self):
        import asyncio

        return asyncio.run(inspection_manager.process_camera_frame(self.inspection_id, object()))


if __name__ == "__main__":
    unittest.main()
