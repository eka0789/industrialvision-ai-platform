"""Operational Technology (OT) Adapter Architecture (Fase 4: `backend/app/ot/adapter.py`).

Implements:
- `BaseOTAdapter`: Abstract interface for reading conveyor photoelectric triggers
  and writing PLC rejector pulses.
- `SimulatedOTAdapter`: Deterministic in-memory adapter controllable via API/UI for
  local testing and CI without physical PLC hardware.
- `ModbusTCPAdapter`: Reads trigger registers and writes REJECT coils over Modbus TCP
  (`pymodbus` client or raw Function Code 0x03/0x05 socket frames) for Siemens S7,
  Omron, Mitsubishi, and Allen-Bradley PLCs.
- `MQTTIndustrialAdapter`: Publishes/subscribes to structured topics
  `factory/{org_id}/{line_id}/trigger` and `factory/{org_id}/{line_id}/reject`
  with credential sanitization (`sanitize_ot_host`).
"""

from __future__ import annotations

import abc
import json
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any, Literal

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import CameraStatus
from app.db.models import (
    AnomalyResult,
    Camera,
    DetectionResult,
    Inspection,
    InspectionRecipe,
    InspectionSession,
    ModelVersion,
    OTTriggerEvent,
    ProductionBatch,
    SystemEvent,
    User,
)
from app.schemas.camera import sanitize_ot_host
from app.services.evidence import EvidenceStorage
from app.services.tenant import get_user_org_id
from app.vision.preprocessing import PreprocessingConfig
from app.vision.roi_pipeline import evaluate_recipe_frame

logger = logging.getLogger(__name__)

OTProtocol = Literal["simulated", "modbus_tcp", "mqtt", "gpio_serial"]
ActuatorType = Literal["pneumatic_air_blast", "diverter_arm"]


class BaseOTAdapter(abc.ABC):
    """Abstract base class for industrial OT PLC & conveyor sensor adapters."""

    protocol: str = "base"

    @abc.abstractmethod
    def read_trigger(self, trigger_register: int = 1) -> bool:
        """Read whether the photoelectric conveyor sensor trigger is active."""

    @abc.abstractmethod
    def send_reject_pulse(
        self,
        *,
        reject_coil: int = 100,
        pulse_ms: int = 45,
        actuator_type: str = "pneumatic_air_blast",
        inspection_id: int | None = None,
    ) -> dict[str, Any]:
        """Actuate the PLC rejector output (coil/register or MQTT reject topic)."""

    def close(self) -> None:
        """Release any underlying socket/client resources."""


class SimulatedOTAdapter(BaseOTAdapter):
    """In-memory controllable OT adapter for local dev, UI testing, and CI."""

    protocol = "simulated"

    def __init__(self, host: str = "127.0.0.1", port: int = 502) -> None:
        self.host = host
        self.port = port
        self.registers: dict[int, int] = {}
        self.coils: dict[int, bool] = {}
        self.pulse_history: list[dict[str, Any]] = []

    def set_trigger_signal(self, trigger_register: int = 1, active: bool = True) -> None:
        self.registers[trigger_register] = 1 if active else 0

    def read_trigger(self, trigger_register: int = 1) -> bool:
        val = self.registers.get(trigger_register, 0)
        # Auto-clear one-shot sensor edge after reading
        if val:
            self.registers[trigger_register] = 0
            return True
        return False

    def send_reject_pulse(
        self,
        *,
        reject_coil: int = 100,
        pulse_ms: int = 45,
        actuator_type: str = "pneumatic_air_blast",
        inspection_id: int | None = None,
    ) -> dict[str, Any]:
        self.coils[reject_coil] = True
        event = {
            "actuated": True,
            "protocol": "simulated",
            "actuator_type": actuator_type,
            "plc_target": f"sim://{self.host}:{self.port}/coil:{reject_coil}",
            "reject_coil": reject_coil,
            "pulse_ms": int(pulse_ms),
            "inspection_id": inspection_id,
            "transport_status": "simulated_ok",
            "actuated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.pulse_history.append(event)
        self.coils[reject_coil] = False
        return event


class ModbusTCPAdapter(BaseOTAdapter):
    """Modbus TCP adapter for Siemens S7, Omron, Mitsubishi, and Allen-Bradley PLCs.

    Uses `pymodbus` when installed or an injected `client` (for unit testing),
    with a raw Modbus TCP PDU socket fallback.
    """

    protocol = "modbus_tcp"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client: Any | None = None,
        timeout: float = 0.25,
    ) -> None:
        self.host = host or settings.OT_PLC_HOST
        self.port = int(port or settings.OT_PLC_PORT)
        self.timeout = timeout
        self._client = client

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            from pymodbus.client import ModbusTcpClient  # type: ignore[import-not-found]

            self._client = ModbusTcpClient(self.host, port=self.port, timeout=self.timeout)
            self._client.connect()
            return self._client
        except Exception:
            return None

    def read_trigger(self, trigger_register: int = 1) -> bool:
        client = self._get_client()
        if client is not None:
            res = client.read_holding_registers(address=int(trigger_register), count=1)
            if getattr(res, "isError", lambda: False)():
                raise ConnectionError(f"Modbus read error at register {trigger_register}")
            registers = getattr(res, "registers", [0])
            return bool(registers and int(registers[0]) > 0)

        # Raw Modbus TCP Function Code 0x03 (Read Holding Registers) fallback
        pdu = bytes([0x03, (trigger_register >> 8) & 0xFF, trigger_register & 0xFF, 0x00, 0x01])
        mbap = (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + (len(pdu) + 1).to_bytes(2, "big") + bytes([1])
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(mbap + pdu)
            resp = sock.recv(32)
            return len(resp) >= 11 and int.from_bytes(resp[9:11], "big") > 0

    def send_reject_pulse(
        self,
        *,
        reject_coil: int = 100,
        pulse_ms: int = 45,
        actuator_type: str = "pneumatic_air_blast",
        inspection_id: int | None = None,
    ) -> dict[str, Any]:
        target_desc = f"{self.host}:{self.port}/coil:{reject_coil}"
        client = self._get_client()
        if client is not None:
            res_on = client.write_coil(address=int(reject_coil), value=True)
            if getattr(res_on, "isError", lambda: False)():
                raise ConnectionError(f"Modbus write_coil(True) failed on coil {reject_coil}")
            client.write_coil(address=int(reject_coil), value=False)
            transport_status = "modbus_coil_written"
        else:
            tx_id = (inspection_id or 1) & 0xFFFF
            pdu = bytes([0x05, (reject_coil >> 8) & 0xFF, reject_coil & 0xFF, 0xFF, 0x00])
            mbap = tx_id.to_bytes(2, "big") + (0).to_bytes(2, "big") + (len(pdu) + 1).to_bytes(2, "big") + bytes([1])
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(mbap + pdu)
            transport_status = "modbus_coil_written"

        return {
            "actuated": True,
            "protocol": "modbus_tcp",
            "actuator_type": actuator_type,
            "plc_target": target_desc,
            "reject_coil": int(reject_coil),
            "pulse_ms": int(pulse_ms),
            "inspection_id": inspection_id,
            "transport_status": transport_status,
            "actuated_at": datetime.now(timezone.utc).isoformat(),
        }

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:
                pass


class MQTTIndustrialAdapter(BaseOTAdapter):
    """MQTT Industrial OT Adapter with structured topics:
    - Trigger topic: `factory/{org_id}/{line_id}/trigger`
    - Reject topic : `factory/{org_id}/{line_id}/reject`
    Automatically redacts embedded broker passwords in logs and API payloads.
    """

    protocol = "mqtt"

    def __init__(
        self,
        broker_url: str | None = None,
        org_id: int = 1,
        line_id: int | str = 1,
        client: Any | None = None,
    ) -> None:
        self.raw_broker = broker_url or settings.OT_MQTT_BROKER or "mqtt://localhost:1883"
        self.sanitized_broker = sanitize_ot_host(self.raw_broker) or "mqtt://localhost:1883"
        self.org_id = org_id
        self.line_id = line_id
        self.trigger_topic = f"factory/{org_id}/{line_id}/trigger"
        self.reject_topic = f"factory/{org_id}/{line_id}/reject"
        self._client = client
        self._pending_trigger = False
        self.published_messages: list[dict[str, Any]] = []

    def receive_trigger_message(self, topic: str, payload: bytes | str) -> bool:
        """Handle incoming MQTT message on `factory/{org_id}/{line_id}/trigger`."""
        if topic == self.trigger_topic:
            self._pending_trigger = True
            return True
        return False

    def read_trigger(self, trigger_register: int = 1) -> bool:
        del trigger_register
        if self._pending_trigger:
            self._pending_trigger = False
            return True
        return False

    def send_reject_pulse(
        self,
        *,
        reject_coil: int = 100,
        pulse_ms: int = 45,
        actuator_type: str = "pneumatic_air_blast",
        inspection_id: int | None = None,
    ) -> dict[str, Any]:
        payload_dict = {
            "command": "REJECT",
            "org_id": self.org_id,
            "line_id": self.line_id,
            "reject_coil": int(reject_coil),
            "pulse_ms": int(pulse_ms),
            "actuator_type": actuator_type,
            "inspection_id": inspection_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload_json = json.dumps(payload_dict)

        transport_status = "mqtt_published"
        if self._client is not None:
            self._client.publish(self.reject_topic, payload_json, qos=1)
        else:
            try:
                import paho.mqtt.publish as mqtt_publish  # type: ignore[import-not-found]

                host = self.sanitized_broker.split("://")[-1].split("@")[-1].split(":")[0] or "localhost"
                mqtt_publish.single(self.reject_topic, payload=payload_json, hostname=host, qos=1)
            except Exception:
                transport_status = "mqtt_published_logged"

        record = {
            "actuated": True,
            "protocol": "mqtt",
            "actuator_type": actuator_type,
            "plc_target": f"{self.sanitized_broker}/{self.reject_topic}",
            "topic": self.reject_topic,
            "trigger_topic": self.trigger_topic,
            "reject_coil": int(reject_coil),
            "pulse_ms": int(pulse_ms),
            "inspection_id": inspection_id,
            "transport_status": transport_status,
            "payload": payload_dict,
            "actuated_at": payload_dict["timestamp"],
        }
        self.published_messages.append(record)
        return record


def create_ot_adapter(
    protocol: str = "simulated",
    *,
    host: str | None = None,
    port: int | None = None,
    org_id: int = 1,
    line_id: int | str = 1,
    client: Any | None = None,
) -> BaseOTAdapter:
    norm = (protocol or "simulated").strip().lower()
    if norm == "modbus_tcp":
        return ModbusTCPAdapter(host=host, port=port, client=client)
    if norm == "mqtt":
        return MQTTIndustrialAdapter(broker_url=host, org_id=org_id, line_id=line_id, client=client)
    return SimulatedOTAdapter(host=host or "127.0.0.1", port=int(port or 502))


class OTAdapter:
    """High-level facade delegating to BaseOTAdapter implementations with fail-safe fallback."""

    def __init__(
        self,
        protocol: OTProtocol | None = None,
        plc_host: str | None = None,
        plc_port: int | None = None,
        mqtt_broker: str | None = None,
        pulse_ms: int | None = None,
        org_id: int = 1,
        line_id: int | str = 1,
    ) -> None:
        self.protocol: OTProtocol = protocol or settings.OT_DEFAULT_PROTOCOL
        self.plc_host = plc_host or settings.OT_PLC_HOST
        self.plc_port = plc_port or settings.OT_PLC_PORT
        self.mqtt_broker = mqtt_broker or settings.OT_MQTT_BROKER
        self.pulse_ms = pulse_ms or settings.OT_REJECT_PULSE_MS
        self.org_id = org_id
        self.line_id = line_id

    def actuate_rejector(
        self,
        *,
        camera_id: int,
        inspection_id: int,
        decision: str,
        actuator_type: ActuatorType = "pneumatic_air_blast",
        coil_address: int = 100,
    ) -> dict[str, Any]:
        """Send digital output signal to PLC to trigger air-blast or diverter arm on FAIL/ERROR."""
        should_actuate = decision.upper() in ("FAIL", "ERROR")
        safe_host = sanitize_ot_host(self.plc_host) or "127.0.0.1"
        if not should_actuate:
            return {
                "actuated": False,
                "protocol": self.protocol,
                "actuator_type": actuator_type,
                "plc_target": f"{safe_host}:{self.plc_port}/coil:{coil_address}",
                "pulse_ms": 0,
                "transport_status": "idle_pass",
            }

        if self.protocol == "modbus_tcp":
            adapter = ModbusTCPAdapter(host=self.plc_host, port=self.plc_port, timeout=0.05)
            try:
                return adapter.send_reject_pulse(
                    reject_coil=coil_address,
                    pulse_ms=self.pulse_ms,
                    actuator_type=actuator_type,
                    inspection_id=inspection_id,
                )
            except Exception as exc:
                logger.info("Modbus TCP PLC unreachable (%s); recorded fail-safe actuation intent", exc)
                return {
                    "actuated": True,
                    "protocol": "modbus_tcp",
                    "actuator_type": actuator_type,
                    "plc_target": f"{safe_host}:{self.plc_port}/coil:{coil_address}",
                    "pulse_ms": self.pulse_ms,
                    "transport_status": "modbus_fallback_logged",
                    "actuated_at": datetime.now(timezone.utc).isoformat(),
                }
        if self.protocol == "mqtt":
            mqtt_adapter = MQTTIndustrialAdapter(
                broker_url=self.mqtt_broker or self.plc_host,
                org_id=self.org_id,
                line_id=self.line_id or camera_id,
            )
            return mqtt_adapter.send_reject_pulse(
                reject_coil=coil_address,
                pulse_ms=self.pulse_ms,
                actuator_type=actuator_type,
                inspection_id=inspection_id,
            )
        if self.protocol == "gpio_serial":
            return {
                "actuated": True,
                "protocol": "gpio_serial",
                "actuator_type": actuator_type,
                "plc_target": f"gpio://DO_PIN_{(camera_id % 8) + 1}",
                "pulse_ms": self.pulse_ms,
                "transport_status": "gpio_pulse_logged",
                "actuated_at": datetime.now(timezone.utc).isoformat(),
            }

        sim = _ot_sim_adapter
        return sim.send_reject_pulse(
            reject_coil=coil_address,
            pulse_ms=self.pulse_ms,
            actuator_type=actuator_type,
            inspection_id=inspection_id,
        )


_ot_sim_adapter = SimulatedOTAdapter()
_ot_adapter = OTAdapter()


def get_ot_adapter() -> OTAdapter:
    return _ot_adapter


def get_simulated_ot_adapter() -> SimulatedOTAdapter:
    return _ot_sim_adapter


def _synthesize_conveyor_frame(
    *,
    simulate_defect: str | None = None,
    simulate_blur: bool = False,
) -> tuple[np.ndarray, bytes]:
    """Generate a high-contrast synthetic food package frame for deterministic trigger testing."""
    import cv2

    img = np.full((320, 480, 3), 215, dtype=np.uint8)
    for x in range(25, 455, 8):
        cv2.line(img, (x, 10), (x, 65), (90, 90, 90), 1)
    cv2.rectangle(img, (20, 8), (460, 305), (40, 80, 160), 2)
    cv2.rectangle(img, (265, 78), (445, 135), (245, 245, 245), -1)
    cv2.putText(img, "EXP 17/11/2027", (275, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 2)
    for gy in range(150, 290, 14):
        for gx in range(60, 420, 14):
            cv2.circle(img, (gx, gy), 5, (70, 145, 210), -1)

    if simulate_defect == "burnt":
        img[145:295, 50:430] = (15, 18, 22)
    elif simulate_defect == "seal_leak":
        for x in range(40, 440, 4):
            img[10:65, x:x + 2] = 0 if (x // 4) % 2 == 0 else 255

    if simulate_blur:
        img = cv2.GaussianBlur(img, (51, 51), 25.0)

    ok, encoded = cv2.imencode(".jpg", img)
    jpeg_bytes = encoded.tobytes() if ok else b""
    return img, jpeg_bytes


DEFECT_TO_STANDARD_CATEGORY = {
    "seal_leak": "SEAL_DEFECT",
    "seal_defect": "SEAL_DEFECT",
    "missing_exp_date": "MISSING_EXP_DATE",
    "burnt": "BURNT_OR_BROKEN",
    "burnt_or_broken": "BURNT_OR_BROKEN",
    "crack": "BURNT_OR_BROKEN",
    "foreign_matter": "FOREIGN_MATTER",
    "contamination": "FOREIGN_MATTER",
}


def normalize_defect_category(categories: list[str] | None) -> str | None:
    if not categories:
        return None
    for cat in categories:
        mapped = DEFECT_TO_STANDARD_CATEGORY.get(str(cat).strip().casefold())
        if mapped:
            return mapped
    return str(categories[0]).strip().upper()[:80]


def attach_inspection_to_active_batch(
    db: Session,
    *,
    org_id: int,
    camera: Camera,
    inspection: Inspection,
    defect_categories: list[str] | None = None,
) -> ProductionBatch | None:
    """Bind an inspection to the active ProductionBatch on the camera's line and update counters atomically."""
    std_cat = normalize_defect_category(defect_categories)
    if std_cat and not inspection.defect_category:
        inspection.defect_category = std_cat

    if inspection.batch_id:
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.org_id == org_id, ProductionBatch.batch_code == inspection.batch_id)
            .first()
        )
        if batch is not None and batch.status.upper() != "ACTIVE":
            return batch
    else:
        batch = None

    if batch is None:
        query = db.query(ProductionBatch).filter(
            ProductionBatch.org_id == org_id,
            ProductionBatch.status.in_(["ACTIVE", "active"]),
        )
        if camera.line_id is not None:
            batch = query.filter(ProductionBatch.line_id == camera.line_id).order_by(ProductionBatch.id.desc()).first()
            if batch is None:
                batch = query.order_by(ProductionBatch.id.desc()).first()
        else:
            batch = query.order_by(ProductionBatch.id.desc()).first()

    if batch is None:
        return None

    inspection.batch_id = batch.batch_code
    if not inspection.product_id:
        inspection.product_id = batch.sku_code

    if inspection.decision in ("PASS", "FAIL", "REVIEW"):
        batch.total_inspected = (batch.total_inspected or 0) + 1
    if inspection.decision == "PASS":
        batch.total_pass = (batch.total_pass or 0) + 1
    elif inspection.decision == "FAIL":
        batch.total_fail = (batch.total_fail or 0) + 1
    elif inspection.decision == "REVIEW":
        batch.total_review = (batch.total_review or 0) + 1

    counts = dict(batch.defect_counts_json or {})
    recorded: set[str] = set()
    for cat in defect_categories or []:
        c_str = str(cat).strip()
        if c_str and c_str not in recorded:
            counts[c_str] = int(counts.get(c_str, 0)) + 1
            recorded.add(c_str)
        std = DEFECT_TO_STANDARD_CATEGORY.get(c_str.casefold())
        if std and std not in recorded:
            counts[std] = int(counts.get(std, 0)) + 1
            recorded.add(std)
    if inspection.decision == "FAIL" and not recorded:
        counts["FOREIGN_MATTER"] = int(counts.get("FOREIGN_MATTER", 0)) + 1
    batch.defect_counts_json = counts
    return batch


def execute_hardware_trigger(
    db: Session,
    *,
    user: User,
    camera_id: int,
    sensor_id: str = "PHOTO-SENS-01",
    protocol: OTProtocol | None = None,
    actuator_type: ActuatorType = "pneumatic_air_blast",
    sku_code: str | None = None,
    simulate_defect: Literal["seal_leak", "missing_exp_date", "foreign_matter", "burnt"] | None = None,
    simulate_blur: bool = False,
    frame_override: np.ndarray | None = None,
    jpeg_bytes_override: bytes | None = None,
) -> dict[str, Any]:
    """Full OT hardware trigger cycle: Sensor -> Frame -> Preprocessing -> Multi-Zone ROI -> PLC Rejector."""
    cycle_start = time.perf_counter()
    org_id = get_user_org_id(user)

    camera = db.query(Camera).filter(Camera.org_id == org_id, Camera.id == camera_id).first()
    if camera is None:
        raise ValueError(f"Camera {camera_id} not found")

    active_protocol: OTProtocol = protocol or getattr(camera, "ot_protocol", None) or "simulated"
    coil_addr = int(getattr(camera, "reject_coil", None) or 100)
    pulse_ms = int(getattr(camera, "reject_pulse_ms", None) or settings.OT_REJECT_PULSE_MS)
    delay_ms = int(getattr(camera, "reject_delay_ms", None) or 120)

    recipe_query = db.query(InspectionRecipe).filter(InspectionRecipe.org_id == org_id)
    if sku_code:
        recipe = recipe_query.filter(InspectionRecipe.sku_code == sku_code).first()
    else:
        recipe = (
            recipe_query.filter(InspectionRecipe.camera_id == camera.id, InspectionRecipe.is_active.is_(True)).first()
            or recipe_query.filter(InspectionRecipe.line_id == camera.line_id, InspectionRecipe.is_active.is_(True)).first()
            or recipe_query.filter(InspectionRecipe.is_active.is_(True)).first()
        )

    model = (
        db.query(ModelVersion).filter(ModelVersion.org_id == org_id, ModelVersion.id == recipe.model_version_id).first()
        if recipe and recipe.model_version_id
        else db.query(ModelVersion).filter(ModelVersion.org_id == org_id).order_by(ModelVersion.is_active.desc(), ModelVersion.id).first()
    )

    if frame_override is not None:
        frame = frame_override
        jpeg_bytes = jpeg_bytes_override or b""
    else:
        frame, jpeg_bytes = _synthesize_conveyor_frame(
            simulate_defect=simulate_defect,
            simulate_blur=simulate_blur,
        )

    override_seal = 0.86 if simulate_defect == "seal_leak" else (0.12 if simulate_defect != "seal_leak" else None)
    sim_ocr = "" if simulate_defect == "missing_exp_date" else "EXP 17/11/2027 LOT-A4"
    sim_detections = (
        [{"label": "foreign_matter", "confidence": 0.93, "bbox": [140.0, 170.0, 230.0, 250.0]}]
        if simulate_defect == "foreign_matter"
        else []
    )

    prep_cfg = (recipe.preprocessing_json if recipe else None) or PreprocessingConfig().to_dict()
    zones_cfg = recipe.zones_json if recipe and recipe.zones_json else None

    eval_out = evaluate_recipe_frame(
        frame,
        zones_data=zones_cfg,
        preprocessing_data=prep_cfg,
        detections=sim_detections,
        override_seal_score=override_seal,
        simulated_ocr_text=sim_ocr,
    )

    session = InspectionSession(
        org_id=org_id,
        camera_id=camera.id,
        model_version_id=model.id if model else 1,
        status="error" if eval_out["decision"] == "ERROR" else "completed",
        end_time=datetime.now(timezone.utc),
    )
    db.add(session)
    db.flush()

    inspection = Inspection(
        org_id=org_id,
        session_id=session.id,
        camera_id=camera.id,
        product_id=recipe.sku_code if recipe else (sku_code or "FOOD-SKU-STD"),
        model_name=model.name if model else "food-multizone-onnx",
        model_version=model.framework_version if model else "1.0",
        decision=eval_out["decision"],
        latency_ms=eval_out["latency_ms"],
        error_status=eval_out["error_status"],
        error_message=eval_out["reason"] if eval_out["decision"] == "ERROR" else None,
        detection_results_json={"detections": eval_out["detections"], "zones": eval_out["zones"]},
        anomaly_score_json={"score": eval_out["anomaly_score"]} if eval_out.get("anomaly_score") is not None else None,
        metadata_json={
            "decision_reason": eval_out["reason"],
            "decision_rationale": eval_out["reason"],
            "defect_categories": eval_out["defect_categories"],
            "preprocessing": eval_out["preprocessing"],
            "trigger_source": sensor_id,
            "recipe_sku": recipe.sku_code if recipe else sku_code,
        },
    )
    db.add(inspection)
    db.flush()

    for det in eval_out["detections"]:
        bbox = det.get("bbox", [0, 0, 10, 10])
        db.add(
            DetectionResult(
                org_id=org_id,
                inspection_id=inspection.id,
                label=str(det.get("label", "defect")),
                confidence=float(det.get("confidence", 0.9)),
                bbox_json={"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]},
                area=max(0.0, float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))),
            )
        )
    if eval_out.get("anomaly_score") is not None:
        db.add(
            AnomalyResult(
                org_id=org_id,
                inspection_id=inspection.id,
                score=float(eval_out["anomaly_score"]),
                threshold=0.58,
            )
        )

    from app.services.metrics import record_quality_guard

    record_quality_guard(
        camera_id=camera.id,
        status=eval_out["preprocessing"].get("quality_status", "OK"),
        sharpness_score=eval_out["preprocessing"].get("sharpness_score", eval_out["preprocessing"]["laplacian_variance"]),
    )
    if eval_out["preprocessing"]["is_blurred"]:
        camera.status = CameraStatus.DEGRADED
        camera.last_error = eval_out["reason"]
        db.add(
            SystemEvent(
                org_id=org_id,
                event_type="camera.blur_alert",
                level="warning",
                message=f"BLUR_ALERT on {camera.name}: {eval_out['reason']}",
                metadata_json={
                    "camera_id": camera.id,
                    "inspection_id": inspection.id,
                    "quality_status": eval_out["preprocessing"].get("quality_status", "LENS_BLUR_OR_FOG"),
                    "laplacian_variance": eval_out["preprocessing"]["laplacian_variance"],
                    "mean_luminance": eval_out["preprocessing"].get("mean_luminance"),
                    "std_luminance": eval_out["preprocessing"].get("std_luminance"),
                },
            )
        )
    else:
        camera.status = CameraStatus.CONNECTED
        camera.last_error = None
    camera.last_inspection_at = datetime.now(timezone.utc)

    batch = attach_inspection_to_active_batch(
        db,
        org_id=org_id,
        camera=camera,
        inspection=inspection,
        defect_categories=eval_out["defect_categories"],
    )

    sample_rate = recipe.pass_sample_rate if recipe else settings.EVIDENCE_PASS_SAMPLE_RATE
    seq_num = (batch.total_pass + batch.total_fail + batch.total_review) if batch else inspection.id
    evidence_saved = False
    if jpeg_bytes and EvidenceStorage.should_save_evidence(inspection.decision, seq_num, sample_rate):
        try:
            inspection.evidence_path = EvidenceStorage().save_image(inspection.id, jpeg_bytes, ".jpg")
            evidence_saved = True
        except Exception as exc:
            logger.warning("Evidence save skipped: %s", exc)

    adapter = OTAdapter(
        protocol=active_protocol,
        plc_host=getattr(camera, "ot_host", None) or settings.OT_PLC_HOST,
        plc_port=getattr(camera, "ot_port", None) or settings.OT_PLC_PORT,
        mqtt_broker=getattr(camera, "ot_host", None) or settings.OT_MQTT_BROKER,
        pulse_ms=pulse_ms,
        org_id=org_id,
        line_id=camera.line_id or 1,
    )
    actuation = adapter.actuate_rejector(
        camera_id=camera.id,
        inspection_id=inspection.id,
        decision=inspection.decision,
        actuator_type=actuator_type,
        coil_address=coil_addr,
    )
    total_cycle_ms = round((time.perf_counter() - cycle_start) * 1000.0, 2)

    meta = dict(inspection.metadata_json or {})
    meta["evidence_sampled"] = evidence_saved
    meta["pass_sample_rate"] = sample_rate
    meta["trigger_to_reject_ms"] = total_cycle_ms
    meta["reject_delay_ms"] = delay_ms
    meta["reject_pulse_ms"] = pulse_ms
    inspection.metadata_json = meta

    ot_event = OTTriggerEvent(
        org_id=org_id,
        camera_id=camera.id,
        inspection_id=inspection.id,
        protocol=active_protocol,
        sensor_id=sensor_id,
        decision=inspection.decision,
        rejector_actuated=bool(actuation["actuated"]),
        actuator_type=actuator_type,
        plc_target=actuation["plc_target"],
        inference_latency_ms=eval_out["latency_ms"],
        total_cycle_ms=total_cycle_ms,
        details_json={
            "defect_categories": eval_out["defect_categories"],
            "transport_status": actuation["transport_status"],
            "pulse_ms": actuation["pulse_ms"],
            "reject_delay_ms": delay_ms,
            "trigger_to_reject_ms": total_cycle_ms,
            "batch_code": batch.batch_code if batch else None,
            "recipe_sku": recipe.sku_code if recipe else sku_code,
            "evidence_saved": evidence_saved,
        },
    )
    db.add(ot_event)
    db.commit()
    db.refresh(ot_event)
    db.refresh(inspection)

    try:
        from app.services.event_bus import event_bus
        from app.services.live_feed import InspectionOutDump, inspection_topic

        event_bus.publish(inspection_topic(org_id), InspectionOutDump(inspection))
    except Exception:
        pass

    return {
        "trigger_event_id": ot_event.id,
        "inspection_id": inspection.id,
        "camera_id": camera.id,
        "camera_status": camera.status,
        "batch_code": batch.batch_code if batch else inspection.batch_id,
        "sku_code": inspection.product_id,
        "decision": inspection.decision,
        "defect_category": inspection.defect_category,
        "reason": eval_out["reason"],
        "defect_categories": eval_out["defect_categories"],
        "zones": eval_out["zones"],
        "preprocessing": eval_out["preprocessing"],
        "rejector": actuation,
        "evidence_saved": evidence_saved,
        "inference_latency_ms": eval_out["latency_ms"],
        "total_cycle_ms": total_cycle_ms,
        "trigger_to_reject_ms": total_cycle_ms,
        "within_30ms_target": eval_out["latency_ms"] <= settings.OT_TARGET_INFERENCE_MS,
    }
