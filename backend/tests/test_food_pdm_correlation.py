"""Phase 5 verification tests: Food Processing & Packaging Equipment PdM & Vision-PdM Cross-Correlation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import CameraStatus, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import Camera, ModelVersion, Organization, ProductionLine, Role, User
from app.db.session import get_db
from app.main import create_app
from app.pdm.dataset import FOOD_FAILURE_MODES, FOOD_FEATURES, generate_food_pdm_dataset
from app.pdm.schemas import (
    FOOD_MACHINE_TYPES,
    FoodSensorReadingInput,
    evaluate_food_equipment_anomalies,
)


class FoodPdmCorrelationTestCase(unittest.TestCase):
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

        db = self.session_factory()
        org = Organization(id=1, name="Food Division Plant", slug="food-plant")
        db.add(org)
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        self.owner = User(username="qa-supervisor", hashed_password="", role_id=roles[RoleNames.OWNER].id, is_active=True, org_id=1)
        self.owner.role_name = RoleNames.OWNER
        self.operator = User(username="line-op", hashed_password="", role_id=roles[RoleNames.OPERATOR].id, is_active=True, org_id=1)
        self.operator.role_name = RoleNames.OPERATOR
        db.add_all([self.owner, self.operator])
        db.flush()

        line = ProductionLine(org_id=1, name="Line 01 — Biscuit Flow-Wrap", description="High-speed 120 ppm packaging")
        db.add(line)
        db.flush()

        cam = Camera(
            org_id=1,
            name="Biscuit Flow-Wrap Cam",
            camera_identity="CAM-FOOD-01",
            source_type="simulated",
            line_id=line.id,
            status=CameraStatus.CONNECTED,
            is_active=True,
        )
        model = ModelVersion(
            org_id=1,
            name="food-multizone-yolov8n",
            framework="onnx",
            framework_version="1.16.0",
            source_path="food.onnx",
            task_type="object_detection",
            is_active=True,
            config_json={"reject_labels": ["seal_leak", "missing_exp_date", "foreign_matter", "burnt"], "detection_confidence": 0.5},
        )
        db.add_all([cam, model])
        db.commit()

        self.camera_id = cam.id
        self.owner_headers = {"Authorization": f"Bearer {create_access_token(self.owner.id, RoleNames.OWNER)}"}
        self.op_headers = {"Authorization": f"Bearer {create_access_token(self.operator.id, RoleNames.OPERATOR)}"}
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        self.temp_dir.cleanup()
        self.engine.dispose()

    def override_db(self):
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def test_1_food_machine_schema_and_synthetic_dataset(self) -> None:
        """Verify food equipment types, domain sensor schema, and synthetic food PdM dataset generator."""
        for expected_type in ("HEAT_SEALER", "FLOW_WRAPPER", "CONVEYOR_DRIVE", "BAKING_OVEN", "CHILLER_TUNNEL"):
            self.assertIn(expected_type, FOOD_MACHINE_TYPES)

        payload = FoodSensorReadingInput(
            sealer_temp_c=128.0,
            pneumatic_pressure_bar=4.8,
            conveyor_speed_mpm=135.0,
            motor_current_amp=15.2,
            vibration_mm_s=5.1,
            ambient_humidity_pct=84.0,
        )
        self.assertEqual(payload.effective_motor_current(), 15.2)

        anomalies = evaluate_food_equipment_anomalies(
            machine_type="HEAT_SEALER",
            sealer_temp_c=payload.sealer_temp_c,
            pneumatic_pressure_bar=payload.pneumatic_pressure_bar,
            conveyor_speed_mpm=payload.conveyor_speed_mpm,
            motor_current_amp=payload.effective_motor_current(),
            vibration_mm_s=payload.vibration_mm_s,
            ambient_humidity_pct=payload.ambient_humidity_pct,
        )
        self.assertIn("SEAL_TEMP_LOW", anomalies)
        self.assertIn("PNEUMATIC_DROP", anomalies)
        self.assertIn("CONVEYOR_OVERLOAD", anomalies)
        self.assertIn("BEARING_VIBRATION_HIGH", anomalies)
        self.assertIn("HIGH_AMBIENT_HUMIDITY", anomalies)

        dataset = generate_food_pdm_dataset(rows=250, seed=42)
        self.assertEqual(len(dataset), 250)
        for feat in FOOD_FEATURES:
            self.assertIn(feat, dataset[0])
        self.assertIn("failure_modes", dataset[0])

    def test_2_seal_defect_spike_correlates_with_sealer_temp_drop_and_creates_pending_maintenance(self) -> None:
        """Spike > 3 SEAL_DEFECT in 2 minutes + sealer_temp_c=128°C -> unified diagnosis + pending MaintenanceRequest."""
        # 1) Register HEAT_SEALER machine on the same camera/line
        mac_res = self.client.post(
            "/pdm/machines",
            headers=self.op_headers,
            json={
                "name": "Heat Sealer Bar Line 01",
                "code": "HS-LINE01-A",
                "machine_type": "HEAT_SEALER",
                "camera_id": self.camera_id,
            },
        )
        self.assertEqual(mac_res.status_code, 201, mac_res.text)
        machine_id = mac_res.json()["id"]

        # 2) Record Food Division sensor telemetry with sealer_temp_c=128°C (below normal 140-185°C)
        read_res = self.client.post(
            f"/pdm/machines/{machine_id}/readings",
            headers=self.op_headers,
            json={
                "sealer_temp_c": 128.0,
                "pneumatic_pressure_bar": 5.2,
                "conveyor_speed_mpm": 115.0,
                "motor_current_amp": 9.4,
                "vibration_mm_s": 2.3,
                "ambient_humidity_pct": 64.0,
            },
        )
        self.assertEqual(read_res.status_code, 201, read_res.text)
        food_feats = read_res.json()["food_features"]
        self.assertEqual(food_feats["sealer_temp_c"], 128.0)
        self.assertEqual(food_feats["motor_current_amp"], 9.4)
        self.assertEqual(food_feats["vibration_mm_s"], 2.3)
        self.assertEqual(food_feats["ambient_humidity_pct"], 64.0)

        # 3) Trigger 4 SEAL_DEFECT (seal_leak) inspections within 2 minutes (> 3 spike threshold)
        for _ in range(4):
            trig = self.client.post(
                f"/ot/trigger/{self.camera_id}",
                headers=self.op_headers,
                json={"simulate_defect": "seal_leak"},
            )
            self.assertEqual(trig.status_code, 201, trig.text)

        # 4) Run Vision-PdM cross-correlation endpoint
        corr_res = self.client.post(
            f"/pdm/machines/{machine_id}/correlate",
            headers=self.op_headers,
        )
        self.assertEqual(corr_res.status_code, 200, corr_res.text)
        corr = corr_res.json()
        self.assertTrue(corr["correlated"])
        self.assertTrue(corr["spike_detected"])
        self.assertGreaterEqual(corr["seal_defect_count"], 4)
        self.assertIn(
            "Lonjakan defect kemasan bocor (SEAL_DEFECT) berkorelasi dengan penurunan suhu sealer bar menjadi 128°C",
            corr["diagnosis"],
        )
        self.assertIsNotNone(corr["maintenance_request_id"])

        # 5) Verify automatic MaintenanceRequest is created in 'pending' status (pending human approval)
        req_list = self.client.get("/pdm/requests?status=pending", headers=self.op_headers)
        self.assertEqual(req_list.status_code, 200)
        pending_ids = [r["id"] for r in req_list.json()]
        self.assertIn(corr["maintenance_request_id"], pending_ids)

        # 6) Owner approves the maintenance request (human-in-the-loop approval)
        approve_res = self.client.post(
            f"/pdm/requests/{corr['maintenance_request_id']}/approve",
            headers=self.owner_headers,
            json={"note": "Disetujui tim Engineering — kalibrasi PID heater bar 165°C"},
        )
        self.assertEqual(approve_res.status_code, 200, approve_res.text)
        self.assertEqual(approve_res.json()["status"], "approved")


if __name__ == "__main__":
    unittest.main()
