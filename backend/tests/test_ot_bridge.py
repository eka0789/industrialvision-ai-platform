"""Comprehensive tests for Fase 4: Industrial OT Bridge & Real-Time Rejector Adapter.

Covers:
1. `BaseOTAdapter` implementations (`SimulatedOTAdapter`, `ModbusTCPAdapter` with mock
   Modbus client, and `MQTTIndustrialAdapter` with structured topics
   `factory/{org_id}/{line_id}/trigger` & `.../reject` and credential redaction).
2. `AsyncOTConveyorWorker` (`app/services/ot_worker.py`):
   - Full async cycle: Sensor Trigger -> Deduplication -> Frame Grab -> ONNX/ROI
     Inference -> Delayed Reject Pulse (`reject_delay_ms`) -> `trigger_to_reject_ms`
     persisted in `Inspection.metadata_json`.
   - Fail-safe resilience when PLC connection drops or latency exceeds conveyor budget
     (logs `SystemEvent`, alerts operator, worker never crashes).
3. REST API & RBAC (`/api/v1/ot/cameras/{id}/config`, `/api/v1/ot/cameras/{id}/test-pulse`,
   and `/api/v1/ot/status`):
   - `admin` & `ai_engineer` can configure PLC I/O and fire Test Rejector Pulse (audited).
   - `operator` & `viewer` are denied (`403`) on Test Rejector Pulse.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import CameraStatus, Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import AuditLog, Camera, Inspection, ModelVersion, Organization, ProductionLine, Role, SystemEvent, User
from app.db.session import get_db
from app.main import create_app
from app.ot.adapter import MQTTIndustrialAdapter, ModbusTCPAdapter, SimulatedOTAdapter
from app.services.ot_worker import AsyncOTConveyorWorker


class _MockModbusResponse:
    def __init__(self, registers: list[int] | None = None, error: bool = False):
        self.registers = registers or [0]
        self._error = error

    def isError(self) -> bool:
        return self._error


class MockModbusClient:
    """Mock pymodbus client recording register reads and coil writes."""

    def __init__(self, trigger_values: dict[int, int] | None = None, fail_on_write: bool = False):
        self.trigger_values = dict(trigger_values or {})
        self.fail_on_write = fail_on_write
        self.coil_writes: list[tuple[int, bool]] = []
        self.register_reads: list[int] = []

    def read_holding_registers(self, address: int, count: int = 1) -> _MockModbusResponse:
        del count
        self.register_reads.append(address)
        val = self.trigger_values.pop(address, 0)
        return _MockModbusResponse([val])

    def write_coil(self, address: int, value: bool) -> _MockModbusResponse:
        if self.fail_on_write:
            raise ConnectionError("Modbus TCP connection reset by peer (PLC offline)")
        self.coil_writes.append((address, bool(value)))
        return _MockModbusResponse()


class MockMQTTClient:
    """Mock paho-mqtt client recording published messages."""

    def __init__(self):
        self.published: list[tuple[str, str, int]] = []

    def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        self.published.append((topic, payload, qos))


class OTBridgePhase4Tests(unittest.TestCase):
    def setUp(self):
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
        db.add(Organization(id=1, name="Food Plant OT", slug="food-ot"))
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        def make_user(username: str, role_name: str) -> User:
            u = User(username=username, hashed_password="", role_id=roles[role_name].id, is_active=True, org_id=1)
            u.role_name = role_name
            db.add(u)
            return u

        self.admin = make_user("ot_admin", RoleNames.ADMIN)
        self.engineer = make_user("ot_eng", RoleNames.AI_ENGINEER)
        self.operator = make_user("ot_op", RoleNames.OPERATOR)
        self.viewer = make_user("ot_view", RoleNames.VIEWER)
        db.flush()

        line = ProductionLine(org_id=1, name="Line 01 — High Speed Packaging", description="120 ppm")
        db.add(line)
        db.flush()

        self.cam = Camera(
            org_id=1,
            name="Conveyor Cam 01",
            camera_identity="CAM-OT-01",
            source_type="simulated",
            line_id=line.id,
            status=CameraStatus.CONNECTED,
            is_active=True,
            ot_protocol="modbus_tcp",
            ot_host="192.168.10.50",
            ot_port=502,
            trigger_register=5,
            reject_coil=105,
            reject_delay_ms=15,
            reject_pulse_ms=40,
        )
        self.model = ModelVersion(
            org_id=1,
            name="food-multizone-onnx",
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
        self.line_id = line.id
        self.headers = {
            "admin": {"Authorization": f"Bearer {create_access_token(self.admin.id, RoleNames.ADMIN)}"},
            "eng": {"Authorization": f"Bearer {create_access_token(self.engineer.id, RoleNames.AI_ENGINEER)}"},
            "op": {"Authorization": f"Bearer {create_access_token(self.operator.id, RoleNames.OPERATOR)}"},
            "view": {"Authorization": f"Bearer {create_access_token(self.viewer.id, RoleNames.VIEWER)}"},
        }
        db.close()

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

    def test_1_adapters_simulated_modbus_and_mqtt_with_credential_redaction(self):
        """Verify SimulatedOTAdapter, ModbusTCPAdapter (mock client), and MQTTIndustrialAdapter topics & credential redaction."""
        # 1) SimulatedOTAdapter
        sim = SimulatedOTAdapter()
        self.assertFalse(sim.read_trigger(1))
        sim.set_trigger_signal(1, True)
        self.assertTrue(sim.read_trigger(1))
        self.assertFalse(sim.read_trigger(1), "One-shot trigger edge must auto-clear after read")
        pulse = sim.send_reject_pulse(reject_coil=102, pulse_ms=50, inspection_id=77)
        self.assertTrue(pulse["actuated"])
        self.assertEqual(pulse["reject_coil"], 102)
        self.assertEqual(len(sim.pulse_history), 1)

        # 2) ModbusTCPAdapter with MockModbusClient
        mock_modbus = MockModbusClient(trigger_values={5: 1})
        modbus = ModbusTCPAdapter(host="192.168.10.50", port=502, client=mock_modbus)
        self.assertTrue(modbus.read_trigger(5))
        self.assertEqual(mock_modbus.register_reads, [5])
        mb_pulse = modbus.send_reject_pulse(reject_coil=105, pulse_ms=40, inspection_id=88)
        self.assertEqual(mb_pulse["transport_status"], "modbus_coil_written")
        self.assertEqual(mock_modbus.coil_writes, [(105, True), (105, False)])

        # 3) MQTTIndustrialAdapter with structured topics and password sanitization
        mock_mqtt = MockMQTTClient()
        mqtt = MQTTIndustrialAdapter(
            broker_url="mqtt://plc_user:SuperSecretPassword!@broker.factory.local:1883",
            org_id=1,
            line_id=3,
            client=mock_mqtt,
        )
        self.assertEqual(mqtt.trigger_topic, "factory/1/3/trigger")
        self.assertEqual(mqtt.reject_topic, "factory/1/3/reject")
        self.assertNotIn("SuperSecretPassword!", mqtt.sanitized_broker)
        self.assertIn("[REDACTED]@broker.factory.local:1883", mqtt.sanitized_broker)

        mqtt.receive_trigger_message("factory/1/3/trigger", b'{"sensor":1}')
        self.assertTrue(mqtt.read_trigger())
        mq_pulse = mqtt.send_reject_pulse(reject_coil=110, pulse_ms=35, inspection_id=99)
        self.assertEqual(mq_pulse["topic"], "factory/1/3/reject")
        self.assertNotIn("SuperSecretPassword!", mq_pulse["plc_target"])
        self.assertEqual(len(mock_mqtt.published), 1)
        self.assertEqual(mock_mqtt.published[0][0], "factory/1/3/reject")

    def test_2_async_ot_worker_trigger_to_reject_cycle_dedup_and_fail_safe(self):
        """Verify AsyncOTConveyorWorker full cycle, deduplication, and fail-safe PLC error handling."""
        mock_modbus = MockModbusClient(trigger_values={5: 1})
        modbus_adapter = ModbusTCPAdapter(host="192.168.10.50", port=502, client=mock_modbus)

        worker = AsyncOTConveyorWorker(
            self.cam_id,
            org_id=1,
            user_id=self.admin.id,
            adapter=modbus_adapter,
            session_factory=self.session_factory,
            dedup_window_ms=2000.0,
            max_safe_inference_ms=25.0,
        )

        loop = asyncio.new_event_loop()
        try:
            # 1) Full cycle: Trigger on reg 5 -> FAIL (seal_leak) -> wait reject_delay_ms (15ms) -> write coil 105
            res = loop.run_until_complete(
                worker.process_trigger(simulate_defect="seal_leak")
            )
            self.assertEqual(res["status"], "ok", res)
            self.assertEqual(res["decision"], "FAIL")
            self.assertTrue(res["rejector"]["actuated"])
            self.assertEqual(mock_modbus.coil_writes, [(105, True), (105, False)])
            self.assertGreaterEqual(res["trigger_to_reject_ms"], 10.0)

            # Verify trigger_to_reject_ms is persisted in Inspection.metadata_json
            db = self.session_factory()
            try:
                ins = db.get(Inspection, res["inspection_id"])
                self.assertIsNotNone(ins)
                self.assertIn("trigger_to_reject_ms", ins.metadata_json)
                self.assertEqual(ins.metadata_json["trigger_to_reject_ms"], res["trigger_to_reject_ms"])
            finally:
                db.close()

            # 2) Contact-bounce deduplication within 500ms window
            mock_modbus.trigger_values[5] = 1
            dedup_res = loop.run_until_complete(worker.process_trigger())
            self.assertEqual(dedup_res["status"], "deduplicated")
            self.assertEqual(worker.deduplicated_count, 1)

            # 3) Fail-safe behavior when PLC connection drops during reject write + latency overrun
            worker._last_trigger_ts = None  # reset dedup timer
            mock_modbus.trigger_values[5] = 1
            mock_modbus.fail_on_write = True
            err_res = loop.run_until_complete(
                worker.process_trigger(simulate_defect="seal_leak", simulated_inference_delay_ms=35.0)
            )
            self.assertEqual(err_res["status"], "plc_error")
            self.assertEqual(err_res["stage"], "send_reject_pulse")

            db = self.session_factory()
            try:
                plc_ev = db.query(SystemEvent).filter(SystemEvent.event_type == "ot.plc_connection_error").first()
                lat_ev = db.query(SystemEvent).filter(SystemEvent.event_type == "ot.conveyor_latency_exceeded").first()
                self.assertIsNotNone(plc_ev, "PLC connection failure must be logged to SystemEvent")
                self.assertIsNotNone(lat_ev, "Conveyor latency overrun must be logged to SystemEvent")
            finally:
                db.close()
        finally:
            loop.close()

    def test_3_rest_api_config_credential_redaction_and_test_pulse_rbac_audit(self):
        """Verify PLC I/O config endpoint redacts MQTT passwords and Test Rejector Pulse enforces RBAC + AuditLog."""
        # Configure MQTT broker with embedded credentials via AI Engineer
        cfg_res = self.client.patch(
            f"/api/v1/ot/cameras/{self.cam_id}/config",
            headers=self.headers["eng"],
            json={
                "ot_protocol": "mqtt",
                "ot_host": "mqtt://line_user:TopSecretBrokerPass@mqtt.foodplant.local:1883",
                "ot_port": 1883,
                "trigger_register": 2,
                "reject_coil": 108,
                "reject_delay_ms": 140,
                "reject_pulse_ms": 50,
            },
        )
        self.assertEqual(cfg_res.status_code, 200, cfg_res.text)
        self.assertNotIn("TopSecretBrokerPass", cfg_res.json()["ot_host"])
        self.assertIn("[REDACTED]@mqtt.foodplant.local:1883", cfg_res.json()["ot_host"])

        # Camera GET endpoint also redacts ot_host
        cam_get = self.client.get(f"/cameras/{self.cam_id}", headers=self.headers["op"]).json()
        self.assertNotIn("TopSecretBrokerPass", cam_get["ot_host"])

        # Operator and Viewer cannot run Test Rejector Pulse (403)
        op_pulse = self.client.post(f"/api/v1/ot/cameras/{self.cam_id}/test-pulse", headers=self.headers["op"], json={})
        self.assertEqual(op_pulse.status_code, 403)
        view_pulse = self.client.post(f"/api/v1/ot/cameras/{self.cam_id}/test-pulse", headers=self.headers["view"], json={})
        self.assertEqual(view_pulse.status_code, 403)

        # Admin and AI Engineer CAN run Test Rejector Pulse (200) -> writes AuditLog
        eng_pulse = self.client.post(
            f"/api/v1/ot/cameras/{self.cam_id}/test-pulse",
            headers=self.headers["eng"],
            json={"actuator_type": "diverter_arm", "pulse_ms": 55, "reject_coil": 108},
        )
        self.assertEqual(eng_pulse.status_code, 200, eng_pulse.text)
        self.assertTrue(eng_pulse.json()["rejector"]["actuated"])
        self.assertEqual(eng_pulse.json()["reject_coil"], 108)

        db = self.session_factory()
        try:
            audit = (
                db.query(AuditLog)
                .filter(AuditLog.action == "ot.rejector.test_pulse", AuditLog.user_id == self.engineer.id)
                .first()
            )
            self.assertIsNotNone(audit)
            self.assertEqual(audit.details_json["reject_coil"], 108)
        finally:
            db.close()

        # Check /api/v1/ot/status exposes avg_trigger_to_reject_ms
        status_res = self.client.get("/api/v1/ot/status", headers=self.headers["op"])
        self.assertEqual(status_res.status_code, 200)
        self.assertIsNotNone(status_res.json()["avg_trigger_to_reject_ms"])


if __name__ == "__main__":
    unittest.main()
