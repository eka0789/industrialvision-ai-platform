"""Asynchronous Industrial OT Conveyor Worker (Fase 4: `backend/app/services/ot_worker.py`).

Implements:
1. Hardware trigger listening & contact-bounce deduplication (`dedup_window_ms`).
2. Zero-copy latest-frame grab from the camera buffer -> sub-30ms ONNX / ROI inference.
3. Delayed PLC `REJECT` pulse scheduling (`reject_delay_ms` travel time from camera to
   rejector nozzle, holding coil for `reject_pulse_ms`).
4. End-to-end `trigger_to_reject_ms` persistence in `Inspection.metadata_json` and
   `OTTriggerEvent`.
5. Strict Fail-Safe Behavior: PLC connection drops or conveyor latency budget overruns
   are logged to `SystemEvent` and broadcast as operator alerts over WebSocket without
   ever crashing the worker loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
from sqlalchemy.orm import Session

from app.core.constants import CameraStatus
from app.db.models import Camera, Inspection, OTTriggerEvent, SystemEvent, User
from app.db.session import SessionLocal
from app.ot.adapter import BaseOTAdapter, create_ot_adapter, execute_hardware_trigger
from app.services.event_bus import event_bus
from app.services.live_feed import inspection_topic

logger = logging.getLogger(__name__)


class AsyncOTConveyorWorker:
    """Async worker bridging conveyor photoelectric triggers, Vision AI, and PLC rejectors."""

    def __init__(
        self,
        camera_id: int,
        *,
        org_id: int = 1,
        user_id: int = 1,
        adapter: BaseOTAdapter | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
        frame_provider: Callable[[], np.ndarray] | None = None,
        dedup_window_ms: float = 35.0,
        max_safe_inference_ms: float = 50.0,
    ) -> None:
        self.camera_id = camera_id
        self.org_id = org_id
        self.user_id = user_id
        self._adapter = adapter
        self.session_factory = session_factory
        self.frame_provider = frame_provider
        self.dedup_window_ms = max(0.0, float(dedup_window_ms))
        self.max_safe_inference_ms = max(1.0, float(max_safe_inference_ms))

        self._last_trigger_ts: float | None = None
        self.deduplicated_count: int = 0
        self.processed_count: int = 0
        self.error_count: int = 0
        self._running: bool = False

    def _is_duplicate_trigger(self, now_monotonic: float) -> bool:
        if self._last_trigger_ts is not None:
            elapsed_ms = (now_monotonic - self._last_trigger_ts) * 1000.0
            if elapsed_ms < self.dedup_window_ms:
                self.deduplicated_count += 1
                return True
        self._last_trigger_ts = now_monotonic
        return False

    def _record_system_alert(
        self,
        db: Session,
        event_type: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        db.add(
            SystemEvent(
                org_id=self.org_id,
                event_type=event_type,
                level="error" if "error" in event_type else "warning",
                message=message[:1000],
                metadata_json=metadata,
            )
        )
        db.commit()
        try:
            event_bus.publish(
                inspection_topic(self.org_id),
                {
                    "type": event_type,
                    "org_id": self.org_id,
                    "camera_id": self.camera_id,
                    "message": message,
                    "metadata": metadata,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            pass

    async def process_trigger(
        self,
        *,
        force_trigger: bool = False,
        simulate_defect: str | None = None,
        simulate_blur: bool = False,
        simulated_inference_delay_ms: float = 0.0,
    ) -> dict[str, Any]:
        """Handle one hardware trigger cycle asynchronously with fail-safe error handling."""
        t0 = time.perf_counter()
        db = self.session_factory()
        try:
            camera = db.query(Camera).filter(Camera.org_id == self.org_id, Camera.id == self.camera_id).first()
            if camera is None:
                return {"status": "camera_not_found", "camera_id": self.camera_id}

            adapter = self._adapter or create_ot_adapter(
                protocol=camera.ot_protocol or "simulated",
                host=camera.ot_host,
                port=camera.ot_port,
                org_id=self.org_id,
                line_id=camera.line_id or 1,
            )

            trigger_reg = int(camera.trigger_register if camera.trigger_register is not None else 1)
            reject_coil = int(camera.reject_coil if camera.reject_coil is not None else 100)
            reject_delay_ms = int(camera.reject_delay_ms if camera.reject_delay_ms is not None else 120)
            reject_pulse_ms = int(camera.reject_pulse_ms if camera.reject_pulse_ms is not None else 45)

            # 1) Read sensor trigger register (unless force_trigger is set)
            if not force_trigger:
                try:
                    triggered = adapter.read_trigger(trigger_reg)
                except Exception as exc:
                    self.error_count += 1
                    camera.status = CameraStatus.DEGRADED
                    camera.last_error = f"OT PLC trigger read failed: {exc}"
                    self._record_system_alert(
                        db,
                        "ot.plc_connection_error",
                        f"PLC trigger read failed on {camera.name}: {exc}",
                        {"camera_id": camera.id, "register": trigger_reg, "error": str(exc)[:300]},
                    )
                    return {"status": "plc_error", "stage": "read_trigger", "error": str(exc)}
                if not triggered:
                    return {"status": "no_trigger", "camera_id": camera.id}

            # 2) Deduplicate contact bounce within dedup_window_ms
            if self._is_duplicate_trigger(t0):
                return {
                    "status": "deduplicated",
                    "camera_id": camera.id,
                    "deduplicated_count": self.deduplicated_count,
                }

            # 3) Grab latest frame and execute ONNX / ROI inference
            frame = self.frame_provider() if self.frame_provider is not None else None
            if simulated_inference_delay_ms > 0:
                await asyncio.sleep(simulated_inference_delay_ms / 1000.0)

            user = db.query(User).filter(User.org_id == self.org_id).first() or User(id=self.user_id, org_id=self.org_id, username="ot-worker", hashed_password="", role_id=1)
            trigger_res = execute_hardware_trigger(
                db,
                user=user,
                camera_id=camera.id,
                sensor_id=f"REG-{trigger_reg}",
                protocol="simulated",  # We schedule the real adapter reject pulse below after reject_delay_ms
                simulate_defect=simulate_defect,  # type: ignore[arg-type]
                simulate_blur=simulate_blur,
                frame_override=frame,
            )

            decision = trigger_res["decision"]
            inspection_id = trigger_res["inspection_id"]
            inf_elapsed_ms = float(trigger_res["inference_latency_ms"]) + float(simulated_inference_delay_ms)

            if inf_elapsed_ms > self.max_safe_inference_ms:
                self._record_system_alert(
                    db,
                    "ot.conveyor_latency_exceeded",
                    f"Inference latency {inf_elapsed_ms:.1f} ms exceeded conveyor budget {self.max_safe_inference_ms:.1f} ms on {camera.name}",
                    {
                        "camera_id": camera.id,
                        "inspection_id": inspection_id,
                        "inference_latency_ms": round(inf_elapsed_ms, 2),
                        "budget_ms": self.max_safe_inference_ms,
                    },
                )

            # 4) If FAIL or ERROR, wait reject_delay_ms (conveyor travel to nozzle) and fire PLC REJECT pulse
            #    unless ot_shadow_mode is enabled on the camera (pilot / dry-run).
            shadow_mode = bool(getattr(camera, "ot_shadow_mode", False))
            pulse_info: dict[str, Any] = {
                "actuated": False,
                "shadow_mode": shadow_mode,
                "would_actuate": False,
                "protocol": adapter.protocol,
                "reject_coil": reject_coil,
                "pulse_ms": 0,
                "transport_status": "idle_pass",
            }
            if decision in ("FAIL", "ERROR"):
                if shadow_mode:
                    pulse_info["would_actuate"] = True
                    pulse_info["transport_status"] = "shadow_mode_suppressed"
                else:
                    if reject_delay_ms > 0:
                        await asyncio.sleep(min(reject_delay_ms, 2000) / 1000.0)
                    try:
                        pulse_info = adapter.send_reject_pulse(
                            reject_coil=reject_coil,
                            pulse_ms=reject_pulse_ms,
                            inspection_id=inspection_id,
                        )
                    except Exception as exc:
                        self.error_count += 1
                        camera.status = CameraStatus.DEGRADED
                        camera.last_error = f"OT PLC reject write failed: {exc}"
                        self._record_system_alert(
                            db,
                            "ot.plc_connection_error",
                            f"PLC reject pulse failed on {camera.name} (coil {reject_coil}): {exc}",
                            {
                                "camera_id": camera.id,
                                "inspection_id": inspection_id,
                                "reject_coil": reject_coil,
                                "error": str(exc)[:300],
                            },
                        )
                        return {
                            "status": "plc_error",
                            "stage": "send_reject_pulse",
                            "inspection_id": inspection_id,
                            "decision": decision,
                            "error": str(exc),
                        }

            # 5) Record end-to-end trigger_to_reject_ms into Inspection metadata & OTTriggerEvent
            trigger_to_reject_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            ins_row = db.get(Inspection, inspection_id)
            if ins_row is not None:
                meta = dict(ins_row.metadata_json or {})
                meta["trigger_to_reject_ms"] = trigger_to_reject_ms
                meta["reject_delay_ms"] = reject_delay_ms
                meta["reject_pulse_ms"] = reject_pulse_ms
                meta["ot_protocol"] = adapter.protocol
                meta["rejector_actuated"] = bool(pulse_info.get("actuated"))
                meta["ot_shadow_mode"] = shadow_mode
                meta["would_actuate"] = bool(pulse_info.get("would_actuate", pulse_info.get("actuated")))
                ins_row.metadata_json = meta

            ot_ev = db.get(OTTriggerEvent, trigger_res["trigger_event_id"])
            if ot_ev is not None:
                ot_ev.protocol = adapter.protocol
                ot_ev.rejector_actuated = bool(pulse_info.get("actuated"))
                ot_ev.plc_target = pulse_info.get("plc_target")
                ot_ev.total_cycle_ms = trigger_to_reject_ms
                details = dict(ot_ev.details_json or {})
                details["trigger_to_reject_ms"] = trigger_to_reject_ms
                details["reject_delay_ms"] = reject_delay_ms
                details["transport_status"] = pulse_info.get("transport_status")
                details["shadow_mode"] = shadow_mode
                details["would_actuate"] = bool(pulse_info.get("would_actuate", pulse_info.get("actuated")))
                ot_ev.details_json = details

            db.commit()
            self._last_trigger_ts = time.perf_counter()
            self.processed_count += 1
            return {
                "status": "ok",
                "inspection_id": inspection_id,
                "trigger_event_id": trigger_res["trigger_event_id"],
                "camera_id": camera.id,
                "decision": decision,
                "rejector": pulse_info,
                "inference_latency_ms": round(inf_elapsed_ms, 2),
                "reject_delay_ms": reject_delay_ms,
                "trigger_to_reject_ms": trigger_to_reject_ms,
            }
        except Exception as exc:  # noqa: BLE001 - worker must never crash
            db.rollback()
            self.error_count += 1
            logger.warning("AsyncOTConveyorWorker caught unexpected error: %s", exc)
            return {"status": "worker_error", "error": str(exc)[:400]}
        finally:
            db.close()
