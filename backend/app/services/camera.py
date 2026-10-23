"""Camera streaming service with ONNX inference integration."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional, Dict, Any, AsyncGenerator

import cv2
from fastapi import WebSocket
from PIL import Image

from app.core.config import settings
from app.services.inference import get_inference, predict_frame
from app.db.models import Camera
from app.db.session import get_db
from sqlalchemy.orm import Session


class CameraStreamError(Exception):
    """Raised when camera stream fails."""
    pass


class CameraStreamer:
    """Manages camera video stream and frame processing."""

    def __init__(self, camera: Camera, db: Session):
        self.camera = camera
        self.db = db
        self.cap: Optional[cv2.VideoCapture] = None
        self.running = False
        self.frame_count = 0
        self.last_inference_time = 0
        self.inference_interval = 1.0 / settings.FPS_INPUT if settings.FPS_INPUT else 0.04  # ~25ms

    def open(self) -> bool:
        """Open camera stream based on source type."""
        try:
            source = self.camera.source_url or 0
            if self.camera.source_type == "rtsp":
                self.cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
            elif self.camera.source_type == "usb":
                self.cap = cv2.VideoCapture(int(source))
            elif self.camera.source_type == "file":
                self.cap = cv2.VideoCapture(source)
            else:
                # Simulated camera
                self.cap = cv2.VideoCapture(0)

            if not self.cap.isOpened():
                raise CameraStreamError(f"Cannot open camera source: {source}")

            # Set resolution if available
            if self.camera.resolution:
                try:
                    w, h = self.camera.resolution.split("x")
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(w))
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(h))
                except ValueError:
                    pass

            self.running = True
            return True

        except Exception as e:
            self.running = False
            raise CameraStreamError(f"Failed to open camera: {e}")

    def read_frame(self) -> Optional[np.ndarray]:
        """Read next frame from stream."""
        if not self.cap or not self.cap.isOpened():
            return None
        ret, frame = self.cap.read()
        if not ret:
            return None
        return frame

    def release(self) -> None:
        """Release camera capture."""
        self.running = False
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.cap = None

    async def stream_frames(
        self, websocket: WebSocket, inference: bool = True
    ) -> AsyncGenerator[None, None]:
        """WebSocket streaming loop with optional ONNX inference."""
        await websocket.accept()
        inference_engine = get_inference() if inference else None

        try:
            while self.running:
                frame = self.read_frame()
                if frame is None:
                    await asyncio.sleep(0.05)
                    continue

                self.frame_count += 1

                # Run inference at interval
                if inference and self.frame_count % max(1, int(1 / self.inference_interval)) == 0:
                    result = predict_frame(frame)
                    # Send detection results
                    try:
                        await websocket.send_json({
                            "camera_id": self.camera.id,
                            "frame_id": self.frame_count,
                            "timestamp": time.time(),
                            "detections": result["detections"],
                            "latency_ms": result["latency_ms"],
                        })
                    except Exception:
                        break
                else:
                    # Just send frame metadata
                    try:
                        await websocket.send_json({
                            "camera_id": self.camera.id,
                            "frame_id": self.frame_count,
                            "timestamp": time.time(),
                            "status": "ok",
                        })
                    except Exception:
                        break

                # Control frame rate
                await asyncio.sleep(max(0, self.inference_interval - (time.time() - time.time())))

        except Exception as e:
            print(f"[Camera] Stream error for {self.camera.id}: {e}")
        finally:
            self.release()


def start_camera_stream(camera_id: int, websocket: WebSocket, inference: bool = True) -> CameraStreamer:
    """Initialize and start camera stream for WebSocket."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise ValueError(f"Camera {camera_id} not found")
        streamer = CameraStreamer(camera, db)
        if not streamer.open():
            raise CameraStreamError(f"Could not open camera {camera_id}")
        # Start background streaming task
        asyncio.create_task(streamer.stream_frames(websocket, inference))
        return streamer
    finally:
        db.close()