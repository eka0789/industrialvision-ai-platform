"""ML Laboratory tests (G1/G2): read-only experiment browser API."""

from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path

from app.core.config import settings as app_settings
from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


class ExperimentBrowserTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.db.base import Base
        from app.db.models import Organization, Role, User

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runs_dir = Path(self._tmp.name) / "runs"
        self.runs_dir.mkdir(parents=True)

        # One completed run with an artifact + one failed run without metrics.
        completed = self.runs_dir / "run-completed"
        completed.mkdir()
        artifact = completed / "model.bin"
        artifact.write_bytes(b"artifact-bytes")
        (completed / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "run-completed",
                    "task": "failure_classification",
                    "seed": 42,
                    "status": "completed",
                    "config": {"rows": 100},
                    "dataset": {"name": "ds", "fingerprint": "fp-1", "split": "train/val/test"},
                    "metrics": {"test": {"f1": 0.91}},
                    "reason": None,
                    "created_at": "2026-11-14T00:00:00Z",
                    "completed_at": "2026-11-14T00:10:00Z",
                    "checkpoints": ["model.bin"],
                }
            ),
            encoding="utf-8",
        )
        failed = self.runs_dir / "run-failed"
        failed.mkdir()
        (failed / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "run-failed",
                    "task": "anomaly_patchcore_training",
                    "seed": 7,
                    "status": "failed",
                    "reason": "anomaly runtime unavailable",
                    "metrics": None,
                    "checkpoints": [],
                }
            ),
            encoding="utf-8",
        )

        patcher = unittest.mock.patch.object(app_settings, "TRAINING_RUNS_DIRS", str(self.runs_dir))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.factory()
        db.add(Organization(id=1, name="A", slug="a"))
        role = Role(name=RoleNames.VIEWER, description="")
        db.add(role)
        db.flush()
        user = User(username="viewer", hashed_password="", role_id=role.id, is_active=True, org_id=1)
        user.role_name = RoleNames.VIEWER
        db.add(user)
        db.commit()
        self.user_id = user.id
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
        return {"Authorization": f"Bearer {create_access_token(self.user_id, RoleNames.VIEWER)}"}

    def test_lists_runs_with_checkpoint_checksums(self):
        response = self.client.get("/training/runs", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        runs = {row["run_id"]: row for row in response.json()}
        self.assertIn("run-completed", runs)
        self.assertEqual(runs["run-completed"]["status"], "completed")
        checkpoint = runs["run-completed"]["checkpoints"][0]
        self.assertTrue(checkpoint["exists"])
        self.assertRegex(checkpoint["sha256"], r"^[0-9a-f]{64}$")
        # Failed run shows reason and no metrics — honest display.
        self.assertEqual(runs["run-failed"]["status"], "failed")
        self.assertIn("runtime unavailable", runs["run-failed"]["reason"])
        self.assertIsNone(runs["run-failed"]["metrics"])

    def test_single_run_lookup_and_invalid_ids(self):
        response = self.client.get("/training/runs/run-completed", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_id"], "run-completed")
        # URL-encoded traversal never reaches a valid run: routing rejects it
        # (404) and the handler itself refuses path separators (422, defense
        # in depth for direct scope calls).
        self.assertIn(self.client.get("/training/runs/a%2Fb", headers=self.auth()).status_code, (404, 422))
        self.assertEqual(self.client.get("/training/runs/nope", headers=self.auth()).status_code, 404)

    def test_requires_authentication(self):
        self.assertEqual(self.client.get("/training/runs").status_code, 401)


if __name__ == "__main__":
    unittest.main()
