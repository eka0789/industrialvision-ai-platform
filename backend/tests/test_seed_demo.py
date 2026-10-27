"""Tests for the demo data seeder."""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    AnomalyResult,
    AuditLog,
    Camera,
    DetectionResult,
    Inspection,
    ModelEvaluation,
    ModelVersion,
    ProductionLine,
    Role,
    SystemEvent,
    User,
)
from app.db.session import SessionLocal
from app.seed import _later, reset_database, seed_demo_data
from app.services.evidence import EvidenceStorage


class SeedDemoDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.tmp = Path(tempfile.mkdtemp(prefix="iv-seed-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Mirror the production layout name so reset_database's safety guard
        # (root must literally be called "evidence") engages.
        self.evidence_root = self.tmp / "data" / "evidence"
        self.storage = EvidenceStorage(self.evidence_root)

    def seed(self, inspections: int = 40, **kwargs):
        db = self.factory()
        try:
            return seed_demo_data(
                db,
                inspections=inspections,
                hours=24,
                rng=random.Random(7),
                storage=self.storage,
                **kwargs,
            )
        finally:
            db.close()

    def test_seed_creates_full_demo_dataset(self):
        summary = self.seed(inspections=40)

        db = self.factory()
        try:
            self.assertEqual(summary["inspections"], 40)
            self.assertEqual(db.query(Role).count(), 3)
            self.assertEqual(db.query(User).count(), 3)
            self.assertEqual(db.query(ProductionLine).count(), 3)
            self.assertEqual(db.query(Camera).count(), 6)
            self.assertEqual(db.query(ModelVersion).count(), 4)
            self.assertEqual(db.query(ModelEvaluation).count(), 5)
            self.assertEqual(db.query(Inspection).count(), 40)
            self.assertGreater(db.query(DetectionResult).count(), 0)
            self.assertGreater(db.query(AnomalyResult).count(), 0)
            self.assertGreater(db.query(SystemEvent).count(), 0)
            self.assertGreater(db.query(AuditLog).count(), 0)

            users = {user.username: user.role.name for user in db.query(User).all()}
            self.assertEqual(
                users,
                {"admin": "admin", "operator1": "operator", "aiengineer": "ai_engineer"},
            )

            decisions = {row.decision for row in db.query(Inspection).all()}
            self.assertLessEqual(decisions, {"PASS", "FAIL", "REVIEW", "ERROR"})
            for row in db.query(Inspection).filter(Inspection.decision == "ERROR").all():
                self.assertIsNotNone(row.error_status)

            with_evidence = [
                row for row in db.query(Inspection).all()
                if row.decision != "ERROR"
            ]
            self.assertTrue(with_evidence)
            for row in with_evidence:
                self.assertIsNotNone(row.evidence_path)
                self.assertTrue((self.evidence_root / row.evidence_path).is_file())

            # Cameras keep a realistic last_inspection_at and mostly connected fleet.
            statuses = {camera.status for camera in db.query(Camera).all()}
            self.assertIn("connected", statuses)
        finally:
            db.close()

    def test_seed_without_evidence_skips_images(self):
        summary = self.seed(inspections=20, with_evidence=False)
        self.assertEqual(summary["evidence"], 0)
        db = self.factory()
        try:
            rows = db.query(Inspection).filter(Inspection.decision != "ERROR").all()
            self.assertTrue(rows)
            self.assertTrue(all(row.evidence_path is None for row in rows))
        finally:
            db.close()

    def test_seed_is_additive_without_duplicating_reference_data(self):
        self.seed(inspections=10)
        self.seed(inspections=10)
        db = self.factory()
        try:
            self.assertEqual(db.query(User).count(), 3)
            self.assertEqual(db.query(ModelVersion).count(), 4)
            self.assertEqual(db.query(Camera).count(), 6)
            self.assertEqual(db.query(Inspection).count(), 20)
        finally:
            db.close()

    def test_reset_database_clears_everything(self):
        self.seed(inspections=10)
        db = self.factory()
        try:
            reset_database(db, storage=self.storage)
            for model in (User, Role, Camera, ProductionLine, ModelVersion, Inspection, AuditLog, SystemEvent):
                self.assertEqual(db.query(model).count(), 0)
        finally:
            db.close()
        self.assertFalse(self.evidence_root.exists())
        self.assertTrue(self.tmp.exists())

    def test_later_handles_naive_datetimes_from_sqlite(self):
        aware = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        naive = datetime(2026, 10, 26, 15, 0)
        self.assertEqual(_later(None, aware), aware)
        self.assertEqual(_later(aware, naive), naive)
        self.assertEqual(_later(naive, aware), naive)


if __name__ == "__main__":
    unittest.main()
