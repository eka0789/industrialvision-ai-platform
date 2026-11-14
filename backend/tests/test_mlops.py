"""MLOps tests (E1–E3): stage lifecycle, PSI drift math, MLflow adapter."""

from __future__ import annotations

import unittest
import unittest.mock

import numpy as np

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import ModelEvaluation, ModelVersion, Organization, Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.drift import compute_reference_stats, drift_report, psi


class PsiMathTests(unittest.TestCase):
    def test_identical_distributions_zero(self):
        dist = [0.1] * 10
        self.assertEqual(psi(dist, dist), 0.0)

    def test_known_value_fixture(self):
        # Uniform vs one-sided shift — hand-checkable magnitude.
        expected = [0.1] * 10
        actual = [0.02] * 5 + [0.18] * 5
        value = psi(expected, actual)
        self.assertGreater(value, 0.05)
        self.assertLess(value, 1.0)

    def test_rejects_mismatched_or_empty_inputs(self):
        with self.assertRaises(ValueError):
            psi([0.1, 0.9], [0.5])
        with self.assertRaises(ValueError):
            psi([], [])

    def test_extreme_shift_is_large(self):
        expected = [0.5, 0.5]
        actual = [0.999, 0.001]
        value = psi(expected, actual)
        self.assertGreater(value, 3.0)  # a full mass shift is a huge PSI


class ReferenceStatsTests(unittest.TestCase):
    def test_reference_stats_shape_and_bins(self):
        rng = np.random.default_rng(0)
        samples = rng.normal(50, 10, size=(500, 5)).tolist()
        stats = compute_reference_stats(samples)
        self.assertEqual(stats["n_samples"], 500)
        self.assertEqual(set(stats["features"]), {
            "air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min"
        })
        for feature in stats["features"].values():
            self.assertEqual(len(feature["edges"]), 11)
            self.assertAlmostEqual(sum(feature["proportions"]), 1.0, places=5)


class DriftReportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.factory()
        db.add(Organization(id=1, name="A", slug="a"))
        db.commit()
        db.close()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _model(self, reference=None):
        db = self.factory()
        model = ModelVersion(
            name="m", framework="sklearn", task_type="failure_classification", org_id=1,
            reference_stats_json=reference,
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        model_id = model.id
        db.close()
        return model_id

    def test_missing_reference_is_explicit_error_not_zero(self):
        model_id = self._model(reference=None)
        db = self.factory()
        try:
            with self.assertRaises(ValueError) as ctx:
                drift_report(db, org_id=1, model_id=model_id, live_samples=[[298, 308, 1500, 26, 100]] * 50)
            self.assertIn("no training-time reference stats", str(ctx.exception))
        finally:
            db.close()

    def test_stable_vs_drifted_comparison(self):
        rng = np.random.default_rng(1)
        training = rng.normal(0, 1, size=(800, 5)).tolist()
        model_id = self._model(reference=compute_reference_stats(training))
        db = self.factory()
        try:
            stable = drift_report(db, org_id=1, model_id=model_id, live_samples=rng.normal(0, 1, size=(300, 5)).tolist())
            self.assertFalse(stable["drifted"])
            self.assertLess(stable["overall_psi"], 0.1)

            shifted = drift_report(db, org_id=1, model_id=model_id, live_samples=(rng.normal(0, 1, size=(300, 5)) + 8).tolist())
            self.assertTrue(shifted["drifted"])
            self.assertGreaterEqual(shifted["overall_psi"], 0.25)
            self.assertEqual(shifted["features"]["tool_wear_min"]["level"], "significant")
        finally:
            db.close()

    def test_unknown_model_raises(self):
        db = self.factory()
        try:
            with self.assertRaises(ValueError):
                drift_report(db, org_id=1, model_id=999, live_samples=[[1, 2, 3, 4, 5]])
        finally:
            db.close()


class StageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.factory()
        db.add(Organization(id=1, name="A", slug="a"))
        role = Role(name=RoleNames.ADMIN, description="")
        db.add(role)
        db.flush()
        user = User(username="eng", hashed_password="", role_id=role.id, is_active=True, org_id=1)
        user.role_name = RoleNames.ADMIN
        db.add(user)
        db.flush()
        self.engineer_id = user.id

        self.model_a = ModelVersion(name="a", framework="onnx", task_type="object_detection", org_id=1, stage="development")
        self.model_b = ModelVersion(name="b", framework="onnx", task_type="object_detection", org_id=1, stage="development")
        db.add_all([self.model_a, self.model_b])
        db.flush()
        # Model A carries the required identified evaluation; B does not.
        db.add(ModelEvaluation(
            model_version_id=self.model_a.id, task_type="object_detection",
            metrics_json={"status": "completed", "run_id": "run-1", "split": "train/val/test", "f1": 0.9},
        ))
        db.commit()
        self.ids = {"a": self.model_a.id, "b": self.model_b.id}
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def override_db(self):
        db = self.factory()
        try:
            yield db
        finally:
            db.close()

    def auth(self):
        return {"Authorization": f"Bearer {create_access_token(self.engineer_id, RoleNames.ADMIN)}"}

    def test_promotion_requires_identified_evaluation_for_production(self):
        # Model B has no evaluation → production promotion refused.
        response = self.client.post(f"/models/{self.ids['b']}/promote", headers=self.auth(), json={"stage": "production"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("evaluation", response.json()["detail"].lower())

        # Model A has one → allowed.
        response = self.client.post(f"/models/{self.ids['a']}/promote", headers=self.auth(), json={"stage": "production"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage"], "production")

    def test_max_one_production_per_task_type(self):
        for model_id in ("a", "b"):
            # Give B an evaluation too, promote both.
            db = self.factory()
            from app.db.models import ModelEvaluation

            db.add(ModelEvaluation(
                model_version_id=self.ids[model_id], task_type="object_detection",
                metrics_json={"status": "completed", "run_id": f"run-{model_id}", "split": "s"},
            ))
            db.commit()
            db.close()
            self.client.post(f"/models/{self.ids[model_id]}/promote", headers=self.auth(), json={"stage": "staging"})
            self.client.post(f"/models/{self.ids[model_id]}/promote", headers=self.auth(), json={"stage": "production"})

        db = self.factory()
        try:
            from app.db.models import ModelVersion

            production = db.query(ModelVersion).filter(ModelVersion.stage == "production").all()
            self.assertEqual(len(production), 1, "only one production model per task type")
            demoted = db.query(ModelVersion).filter(ModelVersion.id == self.ids["a"]).first()
            self.assertEqual(demoted.stage, "staging")
            self.assertEqual(demoted.previous_stage, "production")
        finally:
            db.close()

    def test_rollback_reverts_stage(self):
        self.client.post(f"/models/{self.ids['a']}/promote", headers=self.auth(), json={"stage": "staging"})
        self.client.post(f"/models/{self.ids['a']}/promote", headers=self.auth(), json={"stage": "production"})
        rollback = self.client.post(f"/models/{self.ids['a']}/rollback", headers=self.auth())
        self.assertEqual(rollback.status_code, 200)
        self.assertEqual(rollback.json()["stage"], "staging")
        self.assertEqual(rollback.json()["previous_stage"], "production")

    def test_rollback_without_previous_stage_conflicts(self):
        response = self.client.post(f"/models/{self.ids['b']}/rollback", headers=self.auth())
        self.assertEqual(response.status_code, 409)


class MlflowAdapterTests(unittest.TestCase):
    def test_disabled_without_uri(self):
        from app.training.mlflow_adapter import log_training_run, mlflow_enabled

        with unittest.mock.patch.object(app_settings, "MLFLOW_TRACKING_URI", ""):
            self.assertFalse(mlflow_enabled())
            self.assertFalse(log_training_run(run_id="r", task="t", seed=1, config={}, metrics=None, status="completed"))

    def test_mirror_failure_never_raises(self):
        from app.training.mlflow_adapter import log_training_run

        with unittest.mock.patch.object(app_settings, "MLFLOW_TRACKING_URI", "http://localhost:1"), \
             unittest.mock.patch.dict("sys.modules", {"mlflow": None}):
            self.assertFalse(log_training_run(run_id="r", task="t", seed=1, config={}, metrics={"f1": 1}, status="completed"))


if __name__ == "__main__":
    unittest.main()
