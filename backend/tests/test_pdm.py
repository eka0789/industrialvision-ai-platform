"""Predictive Maintenance tests: dataset, training, inference, API, tenancy."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

from app.core.config import settings as app_settings
from app.pdm.dataset import (
    DATASET_KIND_REAL,
    DATASET_KIND_SYNTHETIC,
    load_or_generate,
    write_split_bundle,
)


class PdmDatasetTests(unittest.TestCase):
    def test_synthetic_generation_is_deterministic_and_valid(self):
        rows_a, kind, source = load_or_generate(rows=300, seed=7)
        rows_b, kind_b, _ = load_or_generate(rows=300, seed=7)
        self.assertEqual(kind, DATASET_KIND_SYNTHETIC)
        self.assertEqual(rows_a, rows_b, "same seed must reproduce rows exactly")
        self.assertGreater(len(rows_a), 0)
        failure_rows = [r for r in rows_a if r["Machine failure"] == 1]
        self.assertGreater(len(failure_rows), 0, "generator must produce failures")

    def test_real_csv_wins_when_present(self):
        import csv
        import tempfile

        from app.pdm.dataset import COLUMNS, _load_real_csv

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai4i2020.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerow(
                    {
                        "UDI": 1,
                        "Product ID": "M1",
                        "Type": "M",
                        "Air temperature [K]": 298.6,
                        "Process temperature [K]": 308.7,
                        "Rotational speed [rpm]": 1400,
                        "Torque [Nm]": 46.3,
                        "Tool wear [min]": 220,
                        "Machine failure": 1,
                        "TWF": 1,
                        "HDF": 0,
                        "PWF": 0,
                        "OSF": 0,
                        "RNF": 0,
                    }
                )
            rows = _load_real_csv(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Machine failure"], 1.0)

        # Missing columns must fail loudly (never silently mis-train).
        bad = Path(path.parent if hasattr(path, "parent") else ".")  # path closed scope guard
        with tempfile.TemporaryDirectory() as tmp2:
            path2 = Path(tmp2) / "bad.csv"
            path2.write_text("UDI,Product ID\n1,P1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                _load_real_csv(path2)

    def test_split_bundle_writes_manifest_and_hashed_splits(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bundle = write_split_bundle(Path(tmp), rows=400, seed=3)
            self.assertEqual(bundle.kind, DATASET_KIND_SYNTHETIC)
            for name in ("train", "val", "test"):
                self.assertTrue(Path(bundle.__getattribute__(f"{name}_path")).is_file())
                self.assertRegex(bundle.split_sha256[name], r"^[0-9a-f]{64}$")
            manifest = json.loads((Path(tmp) / "dataset_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], DATASET_KIND_SYNTHETIC)
            self.assertIn("columns", manifest)


class PdmTrainingTests(unittest.TestCase):
    def test_failure_classifier_trains_selects_and_reports_heldout_metrics(self):
        import tempfile

        from app.pdm.train import train_failure_classifier

        with tempfile.TemporaryDirectory() as tmp:
            summary = train_failure_classifier(run_dir=Path(tmp), rows=400, seed=11)
            self.assertIn("candidates_validation_f1", summary)
            self.assertEqual(len(summary["candidates_validation_f1"]), 3, "must compare at least 3 models")
            self.assertTrue(summary["selected_model"])
            self.assertGreaterEqual(summary["test_metrics"]["test_samples"], 1)
            # Artifact exists and is loadable.
            artifact = Path(app_settings.MODEL_ROOT) / summary["artifact"]
            self.assertTrue(artifact.is_file())
            import joblib

            payload = joblib.load(artifact)
            self.assertIn("model", payload)
            self.assertIn("features", payload)

    def test_rul_regressor_trains_and_reports_mae(self):
        import tempfile

        from app.pdm.train import train_rul_regressor

        with tempfile.TemporaryDirectory() as tmp:
            summary = train_rul_regressor(run_dir=Path(tmp), seed=5)
            self.assertGreaterEqual(len(summary["candidates_validation_mae"]), 3)
            self.assertIn("mae_minutes", summary["test_metrics"])
            self.assertGreaterEqual(summary["test_metrics"]["mae_minutes"], 0.0)

    def test_cli_run_writes_completed_run_and_no_failure_exit(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable, "-m", "app.pdm.train",
                    "--task", "failure_classification",
                    "--rows", "300", "--seed", "9",
                    "--runs-dir", str(Path(tmp) / "runs"),
                ],
                capture_output=True, text=True, timeout=600,
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "completed")
            manifest = json.loads(
                (Path(tmp) / "runs" / payload["run_id"] / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "completed")
            self.assertIsNotNone(manifest["metrics"])


class PdmInferenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.db.base import Base
        from app.db.models import ModelVersion, Organization, Role, User

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()
        org_a = Organization(id=1, name="A", slug="a")
        org_b = Organization(id=2, name="B", slug="b")
        role = Role(name="admin", description="")
        db.add_all([org_a, org_b, role])
        db.flush()
        self.user = User(username="u", hashed_password="", role_id=role.id, is_active=True, org_id=1)
        db.add(self.user)
        db.commit()
        db.close()

        # Keep registry lookups inside the temp MODEL_ROOT.
        patcher = unittest.mock.patch.object(app_settings, "MODEL_ROOT", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _db(self):
        return self.session_factory()

    def test_rules_only_fallback_when_no_artifact(self):
        from app.pdm.inference import MODE_RULES, predict_features

        result = predict_features(
            self._db(),
            org_id=1,
            features={
                "air_temperature_k": 298.0,
                "process_temperature_k": 308.0,
                "rotational_speed_rpm": 1500.0,
                "torque_nm": 26.0,  # power ≈ 4084 W → inside the documented band
                "tool_wear_min": 100.0,
            },
        )
        self.assertEqual(result["mode"], MODE_RULES)
        self.assertIn("no registered PdM artifact", result["note"])
        self.assertEqual(result["health"], 1.0)

        # Documented HDF condition trips the rules path.
        result = predict_features(
            self._db(),
            org_id=1,
            features={
                "air_temperature_k": 300.0,
                "process_temperature_k": 306.0,  # delta < 8.6
                "rotational_speed_rpm": 1300.0,  # < 1380
                "torque_nm": 26.0,  # power ≈ 3543 W → inside the band (HDF only)
                "tool_wear_min": 100.0,
            },
        )
        self.assertIn("HDF", result["failure_modes"])
        self.assertEqual(result["health"], 0.5)

    def test_non_finite_features_error_never_garbage(self):
        from app.pdm.inference import MODE_ERROR, predict_features

        result = predict_features(
            self._db(),
            org_id=1,
            features={
                "air_temperature_k": float("nan"),
                "process_temperature_k": 308.0,
                "rotational_speed_rpm": 1500.0,
                "torque_nm": 40.0,
                "tool_wear_min": 100.0,
            },
        )
        self.assertEqual(result["mode"], MODE_ERROR)
        self.assertIn("non-finite", result["error"])
        self.assertIsNone(result["failure_probability"])

    def test_model_mode_prediction_with_registered_artifact(self):
        import tempfile

        from app.db.models import ModelVersion
        from app.pdm.inference import MODE_ERROR, MODE_MODEL, predict_features
        from app.pdm.train import train_failure_classifier

        with tempfile.TemporaryDirectory() as tmp:
            # Train a tiny classifier into the temp MODEL_ROOT and register it.
            summary = train_failure_classifier(
                run_dir=Path(tmp), rows=300, seed=2, db=self._db(), org_id=1
            )
            db = self._db()
            try:
                row = db.query(ModelVersion).filter(ModelVersion.task_type == "failure_classification").first()
                self.assertIsNotNone(row)
                row.is_active = True
                db.commit()

                result = predict_features(
                    db,
                    org_id=1,
                    features={
                        "air_temperature_k": 298.0,
                        "process_temperature_k": 308.0,
                        "rotational_speed_rpm": 1500.0,
                        "torque_nm": 40.0,
                        "tool_wear_min": 100.0,
                    },
                )
                self.assertEqual(result["mode"], MODE_MODEL)
                self.assertIsNotNone(result["failure_probability"])
                self.assertTrue(math.isfinite(result["failure_probability"]))
                self.assertIsNotNone(result["health"])

                # Corrupt the artifact → ERROR, never silent rules fallback.
                artifact = Path(app_settings.MODEL_ROOT) / summary["artifact"]
                artifact.write_bytes(b"corrupted-joblib-payload")
                broken = predict_features(
                    db,
                    org_id=1,
                    features={
                        "air_temperature_k": 298.0,
                        "process_temperature_k": 308.0,
                        "rotational_speed_rpm": 1500.0,
                        "torque_nm": 40.0,
                        "tool_wear_min": 100.0,
                    },
                )
                self.assertEqual(broken["mode"], MODE_ERROR)
                self.assertTrue(broken["error"])  # corrupt artifact never degrades to rules
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
