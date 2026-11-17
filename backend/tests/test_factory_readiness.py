"""Tests for factory-readiness features:
1. OT Shadow Mode (dry-run rejector suppression in trigger, async worker, and API).
2. Industrial Camera Sources (SimulatedSource headless frames & GenICamSource fail-safe).
3. Threshold calibration sweep (`app.training.calibrate`).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import CameraSourceType, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import Camera, Inspection, OTTriggerEvent, Organization, ProductionLine, Role, User
from app.db.session import get_db
from app.main import create_app
from app.ot.adapter import SimulatedOTAdapter, execute_hardware_trigger
from app.services.camera_sources import GenICamSource, SimulatedSource, create_camera_source
from app.services.ot_worker import AsyncOTConveyorWorker
from app.training.calibrate import sweep_thresholds
from app.training.detection_metrics import Box


class FactoryReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.evidence_patch = patch.object(settings, "EVIDENCE_ROOT", str(Path(self.temp_dir.name) / "evidence"))
        self.evidence_patch.start()
        self.addCleanup(self.evidence_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

        db = self.session_factory()
        db.add(Organization(id=1, name="Factory Plant 01", slug="plant-01"))
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        self.admin = User(username="plant_admin", hashed_password="", role_id=roles[RoleNames.ADMIN].id, is_active=True, org_id=1)
        self.admin.role_name = RoleNames.ADMIN
        db.add(self.admin)
        db.flush()

        line = ProductionLine(org_id=1, name="Line 01")
        db.add(line)
        db.flush()

        self.camera = Camera(
            org_id=1,
            name="CAM-SHADOW-01",
            source_type=CameraSourceType.SIMULATED,
            line_id=line.id,
            ot_protocol="simulated",
            ot_host="127.0.0.1",
            ot_port=502,
            trigger_register=1,
            reject_coil=100,
            reject_delay_ms=0,
            reject_pulse_ms=45,
            ot_shadow_mode=True,
        )
        db.add(self.camera)
        db.commit()
        self.camera_id = self.camera.id
        db.close()

    def test_shadow_mode_suppresses_rejector_in_hardware_trigger(self) -> None:
        db = self.session_factory()
        try:
            res = execute_hardware_trigger(
                db,
                user=self.admin,
                camera_id=self.camera_id,
                simulate_defect="foreign_matter",
            )
            self.assertEqual(res["decision"], "FAIL")
            self.assertFalse(res["rejector"]["actuated"])
            self.assertTrue(res["rejector"]["shadow_mode"])
            self.assertTrue(res["rejector"]["would_actuate"])
            self.assertEqual(res["rejector"]["transport_status"], "shadow_mode_suppressed")

            ins = db.get(Inspection, res["inspection_id"])
            assert ins is not None
            self.assertTrue(ins.metadata_json["ot_shadow_mode"])
            self.assertTrue(ins.metadata_json["would_actuate"])

            ev = db.get(OTTriggerEvent, res["trigger_event_id"])
            assert ev is not None
            self.assertFalse(ev.rejector_actuated)
            self.assertEqual(ev.details_json["transport_status"], "shadow_mode_suppressed")
        finally:
            db.close()

    def test_shadow_mode_suppresses_plc_pulse_in_async_worker(self) -> None:
        sim_adapter = SimulatedOTAdapter()
        sim_adapter.set_trigger_signal(trigger_register=1, active=True)

        worker = AsyncOTConveyorWorker(
            self.camera_id,
            org_id=1,
            user_id=self.admin.id,
            adapter=sim_adapter,
            session_factory=self.session_factory,
        )
        out = asyncio.run(worker.process_trigger(simulate_defect="seal_leak"))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["decision"], "FAIL")
        self.assertFalse(out["rejector"]["actuated"])
        self.assertTrue(out["rejector"]["shadow_mode"])
        self.assertTrue(out["rejector"]["would_actuate"])
        self.assertEqual(out["rejector"]["transport_status"], "shadow_mode_suppressed")
        # Physical/simulated PLC coil must never have received a pulse
        self.assertEqual(len(sim_adapter.pulse_history), 0)

    def test_ot_config_api_toggles_shadow_mode(self) -> None:
        app = create_app()

        def override_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        token = create_access_token(user_id=self.admin.id, role=RoleNames.ADMIN)
        client = TestClient(app)
        resp = client.patch(
            f"/api/v1/ot/cameras/{self.camera_id}/config",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "ot_protocol": "modbus_tcp",
                "ot_host": "192.168.10.50",
                "ot_port": 502,
                "trigger_register": 2,
                "reject_coil": 105,
                "reject_delay_ms": 150,
                "reject_pulse_ms": 40,
                "ot_shadow_mode": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ot_shadow_mode"])
        self.assertEqual(body["ot_protocol"], "modbus_tcp")

    def test_simulated_source_works_headless_and_genicam_fails_safely(self) -> None:
        src = create_camera_source(CameraSourceType.SIMULATED, None, 1)
        self.assertIsInstance(src, SimulatedSource)
        self.assertTrue(src.open())
        ok, frame = src.read_frame()
        self.assertTrue(ok)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (480, 640, 3))
        src.release()

        genicam = create_camera_source(CameraSourceType.GENICAM, "genicam:///opt/pylon/lib/ProducerGEV.cti?id=12345", 2)
        self.assertIsInstance(genicam, GenICamSource)
        cti_path, dev_id = genicam._parse_url()
        self.assertEqual(cti_path, "/opt/pylon/lib/ProducerGEV.cti")
        self.assertEqual(dev_id, "12345")
        with self.assertRaises((RuntimeError, ConnectionError)):
            genicam.open()

    def test_threshold_calibration_sweep(self) -> None:
        gt = {
            "img1.jpg": [Box(0, 0.4, 0.4, 0.6, 0.6)],
            "img2.jpg": [Box(0, 0.4, 0.4, 0.6, 0.6)],
        }
        preds = {
            "img1.jpg": [Box(0, 0.4, 0.4, 0.6, 0.6, conf=0.80)],
            "img2.jpg": [Box(0, 0.4, 0.4, 0.6, 0.6, conf=0.40)],
        }
        report = sweep_thresholds(
            ground_truth=gt,
            predictions=preds,
            thresholds=[0.30, 0.60],
            iou_threshold=0.5,
        )
        self.assertEqual(report["recommended_by_f1"], 0.30)
        self.assertEqual(report["sweep"][0]["escape_rate"], 0.0)
        self.assertEqual(report["sweep"][1]["escape_rate"], 0.5)
