"""Model registry metadata API tests (T20): RBAC, path safety, provenance gates."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import ModelEvaluation, ModelVersion, Role, User
from app.db.session import get_db
from app.main import create_app


class ModelRegistryApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.model_root = Path(self._temp.name) / "models"
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.artifact_bytes = b"onnx-model-fixture-bytes"
        (self.model_root / "detector.onnx").write_bytes(self.artifact_bytes)
        (self.model_root / "weights.pt").write_bytes(b"pt-weights-fixture")
        (self.model_root / "weights.bin").write_bytes(b"not-an-allowed-extension")
        root_patch = patch.object(settings, "MODEL_ROOT", str(self.model_root))
        root_patch.start()
        self.addCleanup(root_patch.stop)

        db = self.session_factory()
        self.tokens: dict[str, str] = {}
        try:
            for role_name in (RoleNames.ADMIN, RoleNames.AI_ENGINEER, RoleNames.OPERATOR):
                role = Role(name=role_name, description=role_name)
                db.add(role)
                db.flush()
                user = User(
                    username=f"user-{role_name}",
                    hashed_password="",
                    role_id=role.id,
                    is_active=True,
                )
                user.set_password("password-12345678")
                db.add(user)
                db.flush()
                self.tokens[role_name] = create_access_token(user.id, role_name)
            db.commit()
        finally:
            db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.dependency_overrides.clear)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def override_db(self):
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def payload(**overrides) -> dict:
        base = {
            "name": "detector-v1",
            "framework": "onnx",
            "framework_version": "1.17.0",
            "task_type": "object_detection",
            "source_path": "detector.onnx",
            "version": "1.0.0",
            "license": "Apache-2.0",
            "classes": ["scratch", "crack"],
            "thresholds": {"confidence": 0.4, "iou": 0.5},
            "provenance": {"dataset": "synthetic-fixture", "split": "val"},
        }
        base.update(overrides)
        return base

    def create(self, role=RoleNames.ADMIN, **overrides) -> dict:
        response = self.client.post(
            "/models/",
            headers={"Authorization": f"Bearer {self.tokens[role]}"},
            json=self.payload(**overrides),
        )
        return response

    def test_create_records_full_registry_metadata_and_checksum(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        expected_checksum = hashlib.sha256(self.artifact_bytes).hexdigest()
        self.assertEqual(body["framework"], "onnx")
        self.assertEqual(body["task_type"], "object_detection")
        self.assertEqual(body["license"], "Apache-2.0")
        self.assertEqual(body["version"], "1.0.0")
        self.assertEqual(body["classes"], ["scratch", "crack"])
        self.assertEqual(body["thresholds"], {"confidence": 0.4, "iou": 0.5})
        self.assertEqual(body["provenance"], {"dataset": "synthetic-fixture", "split": "val"})
        self.assertEqual(body["checksum"], expected_checksum)
        self.assertEqual(body["artifact_id"], f"sha256:{expected_checksum}")
        self.assertFalse(body["is_active"])

        detail = self.client.get(
            f"/models/{body['id']}",
            headers={"Authorization": f"Bearer {self.tokens[RoleNames.OPERATOR]}"},
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["classes"], ["scratch", "crack"])

    def test_artifact_reference_never_accepts_arbitrary_paths(self):
        cases = [
            "/etc/passwd.onnx",                    # absolute
            "../outside.onnx",                     # traversal
            "sub/../../escape.onnx",               # embedded traversal
            "ghost.onnx",                          # missing under MODEL_ROOT
            "weights.bin",                         # unsupported extension
        ]
        for bad_reference in cases:
            with self.subTest(reference=bad_reference):
                response = self.create(name=f"bad-{abs(hash(bad_reference))}", source_path=bad_reference)
                self.assertEqual(response.status_code, 422, response.text)

        db = self.session_factory()
        try:
            self.assertEqual(db.query(ModelVersion).count(), 0)
        finally:
            db.close()

    def test_metadata_validation_and_conflicts(self):
        self.assertEqual(self.create(framework="torchscript").status_code, 422)
        self.assertEqual(self.create(task_type="segmentation").status_code, 422)
        self.assertEqual(self.create(license=None).status_code, 422)
        self.assertEqual(self.create(thresholds={"confidence": 1.5}).status_code, 422)
        self.assertEqual(self.create(classes=["a", "a"]).status_code, 422)

        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 409)

    def test_rbac_write_and_read_access(self):
        self.assertEqual(self.create(role=RoleNames.OPERATOR).status_code, 403)
        anonymous = self.client.get("/models/")
        self.assertEqual(anonymous.status_code, 401)
        listed = self.client.get(
            "/models/",
            headers={"Authorization": f"Bearer {self.tokens[RoleNames.OPERATOR]}"},
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["total"], 0)

    def test_list_filters_and_unknown_filter_rejected(self):
        self.create(task_type="object_detection")
        self.create(
            name="anomaly-v1",
            framework="yolo",
            task_type="anomaly_image",
            source_path="weights.pt",
            is_active=True,
        )
        headers = {"Authorization": f"Bearer {self.tokens[RoleNames.ADMIN]}"}
        self.assertEqual(self.client.get("/models/?active=true", headers=headers).json()["total"], 1)
        self.assertEqual(self.client.get("/models/?framework=yolo", headers=headers).json()["total"], 1)
        self.assertEqual(
            self.client.get("/models/?task_type=object_detection", headers=headers).json()["total"], 1
        )
        self.assertEqual(
            self.client.get("/models/?task_type=unknown", headers=headers).status_code, 422
        )

    def test_activation_keeps_one_active_model_per_task(self):
        first = self.create(name="det-a", is_active=True).json()
        second = self.create(name="det-b", is_active=True).json()
        anomaly = self.create(
            name="anom-a", framework="yolo", task_type="anomaly_image", source_path="weights.pt", is_active=True
        ).json()

        headers = {"Authorization": f"Bearer {self.tokens[RoleNames.AI_ENGINEER]}"}
        first_now = self.client.get(f"/models/{first['id']}", headers=headers).json()
        second_now = self.client.get(f"/models/{second['id']}", headers=headers).json()
        anomaly_now = self.client.get(f"/models/{anomaly['id']}", headers=headers).json()
        self.assertFalse(first_now["is_active"])
        self.assertTrue(second_now["is_active"])
        self.assertTrue(anomaly_now["is_active"])  # different task keeps its own active model

        activated = self.client.post(f"/models/{first['id']}/activate", headers=headers)
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertTrue(activated.json()["is_active"])
        self.assertFalse(
            self.client.get(f"/models/{second['id']}", headers=headers).json()["is_active"]
        )

        deactivated = self.client.post(f"/models/{first['id']}/deactivate", headers=headers)
        self.assertEqual(deactivated.status_code, 200)
        self.assertFalse(deactivated.json()["is_active"])

    def test_patch_updates_metadata_and_revalidates_artifact(self):
        created = self.create().json()
        headers = {"Authorization": f"Bearer {self.tokens[RoleNames.ADMIN]}"}

        patched = self.client.patch(
            f"/models/{created['id']}",
            headers=headers,
            json={"license": "CC-BY-4.0", "provenance": {"dataset": "dagm-2007"}},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["license"], "CC-BY-4.0")
        self.assertEqual(patched.json()["provenance"], {"dataset": "dagm-2007"})

        bad = self.client.patch(
            f"/models/{created['id']}", headers=headers, json={"source_path": "../escape.onnx"}
        )
        self.assertEqual(bad.status_code, 422, bad.text)
        unchanged = self.client.get(f"/models/{created['id']}", headers=headers).json()
        self.assertEqual(unchanged["source_path"], "detector.onnx")

        good = self.client.patch(
            f"/models/{created['id']}", headers=headers, json={"source_path": "weights.pt"}
        )
        self.assertEqual(good.status_code, 200, good.text)
        self.assertEqual(good.json()["source_path"], "weights.pt")
        self.assertEqual(
            good.json()["checksum"], hashlib.sha256(b"pt-weights-fixture").hexdigest()
        )

    def test_evaluation_requires_completed_run_and_identified_split(self):
        created = self.create().json()
        headers = {"Authorization": f"Bearer {self.tokens[RoleNames.AI_ENGINEER]}"}

        not_completed = self.client.post(
            f"/models/{created['id']}/evaluations",
            headers=headers,
            json={
                "task_type": "object_detection",
                "run_id": "run-1",
                "split": "val",
                "status": "running",
                "metrics_json": {"mAP50": 0.9},
            },
        )
        self.assertEqual(not_completed.status_code, 422, not_completed.text)

        missing_identity = self.client.post(
            f"/models/{created['id']}/evaluations",
            headers=headers,
            json={"task_type": "object_detection", "metrics_json": {"mAP50": 0.9}},
        )
        self.assertEqual(missing_identity.status_code, 422, missing_identity.text)

        valid = self.client.post(
            f"/models/{created['id']}/evaluations",
            headers=headers,
            json={
                "task_type": "object_detection",
                "run_id": "run-20261024-001",
                "split": "val",
                "status": "completed",
                "metrics_json": {"precision": 0.8, "recall": 0.7},
                "hardware": "cpu",
                "batch_size": 8,
            },
        )
        self.assertEqual(valid.status_code, 201, valid.text)
        metrics = valid.json()["metrics_json"]
        self.assertEqual(metrics["run_id"], "run-20261024-001")
        self.assertEqual(metrics["split"], "val")
        self.assertEqual(metrics["status"], "completed")

        listed = self.client.get(f"/models/{created['id']}/evaluations", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)

        missing_model = self.client.post(
            "/models/99999/evaluations",
            headers=headers,
            json={
                "task_type": "object_detection",
                "run_id": "r",
                "split": "val",
                "metrics_json": {"x": 1.0},
            },
        )
        self.assertEqual(missing_model.status_code, 404)

    def test_unknown_model_detail_returns_404(self):
        headers = {"Authorization": f"Bearer {self.tokens[RoleNames.ADMIN]}"}
        self.assertEqual(self.client.get("/models/4242", headers=headers).status_code, 404)

    def test_evaluation_rows_are_persisted(self):
        created = self.create().json()
        headers = {"Authorization": f"Bearer {self.tokens[RoleNames.ADMIN]}"}
        self.client.post(
            f"/models/{created['id']}/evaluations",
            headers=headers,
            json={
                "task_type": "object_detection",
                "run_id": "run-x",
                "split": "test",
                "metrics_json": {"f1": 0.5},
            },
        )
        db = self.session_factory()
        try:
            row = db.query(ModelEvaluation).one()
            self.assertEqual(row.metrics_json["run_id"], "run-x")
            self.assertEqual(row.model_version_id, created["id"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
