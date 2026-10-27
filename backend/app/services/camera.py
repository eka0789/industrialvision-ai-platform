"""Camera streaming and session management service."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import cv2
import numpy as np
from fastapi import WebSocket
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Camera, InspectionSession
from app.db.session import SessionLocal
from app.services.camera_sources import CameraSource, create_camera_source, redact_rtsp_url
from app.services.capture_worker import CaptureWorker, CaptureMetrics
from app.services.inference import get_inference, predict_frame
from app.services.runtime_metrics import get_runtime_metrics
from app.services.video_inspection import VideoInspector, VideoInspectionResult


class CameraStreamError(Exception):
    """Raised when camera stream fails."""
    pass


class CameraSessionManager:
    """Manages camera inspection sessions and streaming."""

    def __init__(self):
        self._active_sessions: dict[int, CaptureWorker] = {}
        self._lock = asyncio.Lock()

    async def start_session(
        self,
        camera_id: int,
        db: Session,
        model_version_id: int,
    ) -> InspectionSession:
        """Start a new inspection session for a camera."""
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise ValueError(f"Camera {camera_id} not found")
        if not camera.is_active:
            raise ValueError(f"Camera {camera_id} is not active")

        session = InspectionSession(
            camera_id=camera_id,
            model_version_id=model_version_id,
            status="running",
        )
        db.add(session)
        camera.status = "connected"
        camera.last_error = None
        db.commit()
        db.refresh(session)
        return session

    async def stop_session(
        self,
        session_id: int,
        db: Session,
    ) -> InspectionSession | None:
        """Stop an inspection session."""
        async with self._lock:
            worker = self._active_sessions.pop(session_id, None)
            if worker:
                worker.stop()

        session = db.query(InspectionSession).filter(InspectionSession.id == session_id).first()
        if session:
            session.status = "stopped"
            session.end_time = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            db.commit()
        return session

    async def get_session_status(self, session_id: int, db: Session) -> dict:
        """Get current session status with metrics."""
        session = db.query(InspectionSession).filter(InspectionSession.id == session_id).first()
        if not session:
            return {"error": "Session not found"}

        async with self._lock:
            worker = self._active_sessions.get(session_id)

        return {
            "session_id": session.id,
            "camera_id": session.camera_id,
            "model_version_id": session.model_version_id,
            "status": session.status,
            "start_time": session.start_time.isoformat() if session.start_time else None,
            "end_time": session.end_time.isoformat() if session.end_time else None,
            "metrics": {
                "frames_read": worker.metrics.frames_read if worker else 0,
                "frames_dropped": worker.metrics.frames_dropped if worker else 0,
                "reconnects": worker.metrics.reconnects if worker else 0,
                "read_errors": worker.metrics.read_errors if worker else 0,
                "fps": worker.metrics.fps if worker else 0.0,
                "latency_ms": worker.metrics.latency_ms if worker else 0.0,
            } if worker else None,
        }


class CameraStreamer:
    """Manages camera video stream with inference for WebSocket."""

    def __init__(self, camera: Camera, db: Session):
        self.camera = camera
        self.db = db
        self.source: Optional[CameraSource] = None
        self.worker: Optional[CaptureWorker] = None
        self.running = False
        self.frame_count = 0

    def open(self) -> bool:
        """Open camera stream based on source type."""
        try:
            self.source = create_camera_source(
                self.camera.source_type,
                self.camera.source_url,
                self.camera.id,
            )
            if not self.source.open():
                raise CameraStreamError(f"Cannot open camera source: {redact_rtsp_url(self.camera.source_url)}")

            if self.camera.resolution:
                try:
                    w, h = map(int, self.camera.resolution.split("x"))
                    self.source.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                    self.source.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                except (ValueError, AttributeError):
                    pass

            self.running = True
            return True

        except Exception as e:
            self.running = False
            safe_error = redact_rtsp_url(str(e))
            self.camera.last_error = safe_error
            self.camera.status = "degraded"
            self.db.commit()
            raise CameraStreamError(f"Failed to open camera: {safe_error}")

    def _start_capture_worker(self) -> CaptureWorker:
        """Start bounded capture worker."""
        self.worker = CaptureWorker(
            self.source,
            buffer_size=1,
            read_timeout=2.0,
            no_frame_timeout=5.0,
            max_reconnects=5,
            backoff_base=0.2,
            collector=get_runtime_metrics(),
        )
        self.worker.start()
        return self.worker

    async def stream_frames(
        self,
        websocket: WebSocket,
        inference: bool = True,
        session_id: int | None = None,
        subprotocol: str | None = None,
    ) -> None:
        """WebSocket streaming loop with optional ONNX inference."""
        if not self.running:
            if not self.open():
                await websocket.close(code=4000, reason="Cannot open camera")
                return

        worker = self._start_capture_worker()
        inference_engine = get_inference() if inference else None
        interval = 1.0 / settings.FPS_INPUT if settings.FPS_INPUT else 0.04

        await websocket.accept(subprotocol=subprotocol)

        try:
            while self.running:
                latest = worker.get_latest()
                if latest is None:
                    await asyncio.sleep(0.02)
                    continue

                ts, frame = latest
                self.frame_count += 1

                if inference and self.frame_count % max(1, int(1 / interval)) == 0:
                    try:
                        result = predict_frame(frame)
                        await websocket.send_json({
                            "camera_id": self.camera.id,
                            "session_id": session_id,
                            "frame_id": self.frame_count,
                            "timestamp": ts,
                            "detections": result.get("detections", []),
                            "latency_ms": result.get("latency_ms", 0),
                        })
                    except Exception:
                        break
                else:
                    try:
                        await websocket.send_json({
                            "camera_id": self.camera.id,
                            "session_id": session_id,
                            "frame_id": self.frame_count,
                            "timestamp": ts,
                            "status": "ok",
                        })
                    except Exception:
                        break

                await asyncio.sleep(max(0, interval - (time.time() - time.time())))

        except Exception as e:
            print(f"[Camera] Stream error for {self.camera.id}: {e}")
        finally:
            self.close()

    def close(self) -> None:
        """Stop stream and release resources."""
        self.running = False
        if self.worker:
            self.worker.stop()
        if self.source:
            self.source.release()


camera_session_manager = CameraSessionManager()


def start_camera_stream(
    camera_id: int,
    websocket: WebSocket,
    inference: bool = True,
    session_id: int | None = None,
) -> CameraStreamer:
    """Initialize and start camera stream for WebSocket."""
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise ValueError(f"Camera {camera_id} not found")
        streamer = CameraStreamer(camera, db)
        asyncio.create_task(streamer.stream_frames(websocket, inference, session_id))
        return streamer
    finally:
        db.close()


def run_video_inspection(
    camera_id: int,
    file_path: str,
    frame_interval: int = 1,
    max_frames: int | None = None,
) -> VideoInspectionResult:
    """Run video file inspection synchronously."""
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise ValueError(f"Camera {camera_id} not found")
        inspector = VideoInspector(camera, file_path, frame_interval, max_frames)
        return inspector.run()
    finally:
        db.close()