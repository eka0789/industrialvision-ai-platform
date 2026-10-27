"""Demo live-inspection feed.

When enabled, a background task fabricates a realistic inspection every few
seconds for simulated cameras: it renders a synthetic evidence image, records
session/inspection/detection/anomaly rows, applies the same decision rules as
the real pipeline, and broadcasts the result on the event bus so connected
WebSocket clients see the dashboard update in real time.

Gated by ``DEMO_LIVE_FEED`` (and disabled entirely when ``APP_ENV=test``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import datetime, timezone

from app.core.config import settings
from app.db.models import AnomalyResult, Camera, DetectionResult, Inspection, InspectionSession, ModelVersion, SystemEvent
from app.db.session import SessionLocal
from app.services.decision import decide_inspection
from app.services.evidence import EvidenceStorage
from app.services.event_bus import event_bus
from app.services.inspection_manager import _rules_for_model
from app.services.inspection_serializer import serialize_inspection
from app.services.synthetic_media import render_part_image

logger = logging.getLogger(__name__)

TOPIC_INSPECTIONS = "inspections"
PRODUCT_CODES = ("BRK-2041-A", "BRK-2041-B", "CVR-1180-C", "HSG-0904-A", "SFT-3310-D")


class LiveInspectionFeed:
    """Fabricates periodic inspections for simulated cameras and broadcasts them."""

    def __init__(
        self,
        session_factory=SessionLocal,
        bus=event_bus,
        storage: EvidenceStorage | None = None,
        interval: float | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._storage = storage or EvidenceStorage()
        self._interval = interval if interval is not None else settings.DEMO_FEED_INTERVAL_SECONDS
        self._rng = rng or random.Random()
        self._task: asyncio.Task | None = None
        self._camera_cursor = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="live-inspection-feed")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run(self) -> None:
        logger.info("Demo live feed started (interval=%.1fs)", self._interval)
        while True:
            payload = await asyncio.to_thread(self._generate_one)
            if payload is not None:
                self._bus.publish(TOPIC_INSPECTIONS, payload)
            await asyncio.sleep(self._interval)

    # -- generation --------------------------------------------------------

    def _candidates(self, db) -> list[tuple[Camera, ModelVersion]]:
        cameras = (
            db.query(Camera)
            .filter(Camera.is_active.is_(True), Camera.source_type == "simulated")
            .order_by(Camera.id)
            .all()
        )
        if not cameras:
            return []
        active_models = db.query(ModelVersion).filter(ModelVersion.is_active.is_(True)).all()
        detection_model = next((m for m in active_models if m.task_type == "object_detection"), None)
        anomaly_model = next((m for m in active_models if (m.task_type or "").startswith("anomaly")), None)
        pairs: list[tuple[Camera, ModelVersion]] = []
        for camera in cameras:
            # Deterministic pairing: alternating cameras alternate model kinds,
            # so the feed exercises both decision paths.
            model = detection_model if len(pairs) % 2 == 0 else anomaly_model
            if model is None:
                model = detection_model or anomaly_model
            if model is not None:
                pairs.append((camera, model))
        return pairs

    def _generate_one(self) -> dict | None:
        db = self._session_factory()
        try:
            candidates = self._candidates(db)
            if not candidates:
                return None
            camera, model = candidates[self._camera_cursor % len(candidates)]
            self._camera_cursor += 1

            now = datetime.now(timezone.utc)
            session = InspectionSession(camera_id=camera.id, model_version_id=model.id, start_time=now)
            db.add(session)
            db.flush()

            try:
                rules = _rules_for_model(model)
            except ValueError:
                return None

            if self._rng.random() < 0.02:
                inspection = self._record_error(db, session, camera, model, now)
            else:
                inspection = self._record_result(db, session, camera, model, rules, now)
            db.commit()

            payload = InspectionOutDump(inspection)
            self._touch_system_events(db, inspection)
            db.commit()
            return payload
        except Exception:
            db.rollback()
            logger.exception("Live feed generation failed")
            return None
        finally:
            db.close()

    def _record_error(self, db, session, camera, model, now) -> Inspection:
        inspection = Inspection(
            session_id=session.id,
            camera_id=camera.id,
            product_id=self._rng.choice(PRODUCT_CODES),
            batch_id=self._batch_code(),
            model_name=model.name,
            model_version=model.framework_version,
            decision="ERROR",
            latency_ms=None,
            error_status="inference_failed",
            error_message="Synthetic ONNX session timed out after 15000 ms",
            metadata_json={"decision_reason": "Inference failed"},
            created_at=now,
        )
        session.status = "error"
        session.end_time = now
        camera.last_inspection_at = now
        camera.status = "connected"
        db.add(inspection)
        db.flush()
        return inspection

    def _record_result(self, db, session, camera, model, rules, now) -> Inspection:
        is_anomaly_model = (model.task_type or "").startswith("anomaly")
        reject_labels = sorted(rules.reject_labels) or ["crack"]
        classes = model.classes_json or reject_labels

        detections: list[dict] = []
        anomaly_score: float | None = None
        defect_boxes: list[tuple[float, float, float, float]] = []

        if is_anomaly_model:
            threshold = rules.anomaly_threshold if rules.anomaly_threshold is not None else 0.6
            roll = self._rng.random()
            if roll < 0.68:
                anomaly_score = self._rng.uniform(0.04, max(0.05, threshold - rules.review_band - 0.02))
            elif roll < 0.85:
                anomaly_score = self._rng.uniform(
                    max(0.0, threshold - rules.review_band), max(0.01, threshold - 0.005)
                )
            else:
                anomaly_score = self._rng.uniform(threshold, min(1.0, threshold + 0.3))
        else:
            defect_roll = self._rng.random()
            if defect_roll < 0.12:
                count = self._rng.randint(1, 2)
                for _ in range(count):
                    box = self._random_box()
                    defect_boxes.append(box)
                    detections.append(
                        {
                            "label": self._rng.choice(reject_labels),
                            "confidence": round(self._rng.uniform(rules.detection_confidence + 0.05, 0.97), 3),
                            "bbox": {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]},
                        }
                    )
            elif defect_roll < 0.24:
                box = self._random_box()
                detections.append(
                    {
                        "label": self._rng.choice([c for c in classes if c not in reject_labels] or ["wear"]),
                        "confidence": round(self._rng.uniform(0.3, rules.detection_confidence), 3),
                        "bbox": {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]},
                    }
                )

        decision = decide_inspection(
            detections if detections else ([] if is_anomaly_model else []),
            anomaly_score=anomaly_score,
            rules=rules,
        )

        latency = self._rng.uniform(25, 95) if is_anomaly_model else self._rng.uniform(38, 130)
        inspection = Inspection(
            session_id=session.id,
            camera_id=camera.id,
            product_id=self._rng.choice(PRODUCT_CODES),
            batch_id=self._batch_code(),
            model_name=model.name,
            model_version=model.framework_version,
            decision=decision["decision"],
            latency_ms=round(latency, 2),
            error_status="decision_error" if decision["decision"] == "ERROR" else None,
            metadata_json={"decision_reason": decision["reason"]},
            created_at=now,
        )
        db.add(inspection)
        db.flush()

        for detection in detections:
            bbox = detection["bbox"]
            db.add(
                DetectionResult(
                    inspection_id=inspection.id,
                    label=detection["label"],
                    confidence=detection["confidence"],
                    bbox_json=bbox,
                    area=(bbox["x2"] - bbox["x1"]) * (bbox["y2"] - bbox["y1"]),
                )
            )
        if is_anomaly_model and anomaly_score is not None:
            db.add(
                AnomalyResult(
                    inspection_id=inspection.id,
                    score=round(anomaly_score, 4),
                    threshold=rules.anomaly_threshold,
                )
            )

        inspection.detection_results_json = {"detections": detections}
        inspection.anomaly_score_json = {"score": anomaly_score} if anomaly_score is not None else None

        if decision["decision"] != "ERROR":
            content = render_part_image(defect_boxes, rng=self._rng)
            inspection.evidence_path = self._storage.save_image(inspection.id, content, ".jpg")

        session.status = "completed"
        session.end_time = now
        camera.last_inspection_at = now
        camera.status = "connected"
        camera.last_error = None
        db.flush()
        return inspection

    def _touch_system_events(self, db, inspection: Inspection) -> None:
        if inspection.decision == "FAIL":
            db.add(
                SystemEvent(
                    event_type="inspection.fail",
                    level="warning",
                    message=f"Inspection {inspection.id} rejected on {inspection.camera_id}",
                    metadata_json={"inspection_id": inspection.id, "camera_id": inspection.camera_id},
                )
            )

    def _random_box(self) -> tuple[float, float, float, float]:
        width = self._rng.uniform(34, 90)
        height = self._rng.uniform(26, 70)
        x1 = self._rng.uniform(120, 640 - 120 - width)
        y1 = self._rng.uniform(90, 480 - 110 - height)
        return (round(x1, 1), round(y1, 1), round(x1 + width, 1), round(y1 + height, 1))

    def _batch_code(self) -> str:
        return f"BATCH-{datetime.now(timezone.utc):%Y%m%d}-L{self._rng.randint(1, 3)}"


def InspectionOutDump(inspection: Inspection) -> dict:
    """JSON-safe payload broadcast over the WebSocket live feed."""
    from fastapi.encoders import jsonable_encoder

    return {
        "type": "inspection.created",
        "inspection": jsonable_encoder(serialize_inspection(inspection)),
    }


live_feed = LiveInspectionFeed()
