"""Tests for the demo live-inspection feed."""

from __future__ import annotations

import asyncio
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Inspection, ModelVersion, Role, User
from app.services.evidence import EvidenceStorage
from app.services.event_bus import EventBus
from app.services.live_feed import LiveInspectionFeed, TOPIC_INSPECTIONS


class LiveFeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.tmp = Path(tempfile.mkdtemp(prefix="iv-live-feed-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = EventBus()
        self.feed = LiveInspectionFeed(
            session_factory=self.factory,
            bus=self.bus,
            storage=EvidenceStorage(self.tmp),
            interval=3600,
            rng=random.Random(11),
        )

    def _seed_station(self, *, with_camera: bool = True, with_model: bool = True):
        db = self.factory()
        try:
            if with_model:
                db.add(
                    ModelVersion(
                        name="demo-detector",
                        framework="yolo",
                        framework_version="8.3.47",
                        source_path="demo.onnx",
                        task_type="object_detection",
                        classes_json=["scratch", "dent", "crack"],
                        config_json={"reject_labels": ["crack"], "detection_confidence": 0.5},
                        is_active=True,
                    )
                )
            if with_camera:
                role = Role(name="operator", description="Operator")
                db.add(role)
                db.flush()
                user = User(username="feed-op", hashed_password="", role_id=role.id, is_active=True)
                user.set_password("feed-operator-pass")
                db.add(user)
                db.add(Camera(name="Sim cam", source_type="simulated", is_active=True))
            db.commit()
        finally:
            db.close()

    def test_generate_one_creates_inspection_with_evidence_and_event(self):
        self._seed_station()
        payload = self.feed._generate_one()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "inspection.created")

        inspection_payload = payload["inspection"]
        # Payload must already be JSON-safe (timestamps serialized to strings).
        self.assertIsInstance(inspection_payload["timestamp"], str)
        self.assertIn(inspection_payload["decision"], {"PASS", "FAIL", "REVIEW", "ERROR"})

        db = self.factory()
        try:
            inspections = db.query(Inspection).all()
            self.assertEqual(len(inspections), 1)
            row = inspections[0]
            self.assertEqual(row.decision, inspection_payload["decision"])
            camera = db.query(Camera).first()
            self.assertEqual(camera.status, "connected")
            self.assertIsNotNone(camera.last_inspection_at)
            if row.decision != "ERROR":
                self.assertIsNotNone(row.evidence_path)
                self.assertTrue((self.tmp / row.evidence_path).is_file())
            else:
                self.assertEqual(row.error_status, "inference_failed")
        finally:
            db.close()

    def test_generate_one_without_stations_returns_none(self):
        self._seed_station(with_camera=False)
        self.assertIsNone(self.feed._generate_one())

    def test_generate_one_without_active_model_returns_none(self):
        self._seed_station(with_model=False)
        self.assertIsNone(self.feed._generate_one())

    def test_run_publishes_to_event_bus(self):
        self._seed_station()
        queue = self.bus.subscribe(TOPIC_INSPECTIONS)

        async def scenario():
            self.feed.start()
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=10)
            finally:
                await self.feed.stop()
            return payload

        payload = asyncio.run(scenario())
        self.assertEqual(payload["type"], "inspection.created")
        self.assertEqual(self.feed._task, None)


if __name__ == "__main__":
    unittest.main()
