"""Video file inspection: validate, process frames, aggregate results."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from app.services.camera_sources import VideoFileSource
from app.services.capture_worker import CaptureWorker, CaptureMetrics
from app.services.inference import predict_frame, get_inference
from app.core.config import settings
from app.db.models import Camera


@dataclass
class VideoInspectionResult:
    camera_id: int
    total_frames: int
    processed_frames: int
    dropped_frames: int
    fps: float
    avg_latency_ms: float
    duration_sec: float
    detections: list[dict]
    errors: list[str]


class VideoInspector:
    """Process video file at configurable frame intervals."""

    def __init__(
        self,
        camera: Camera,
        file_path: str,
        frame_interval: int = 1,
        max_frames: int | None = None,
    ):
        self.camera = camera
        self.file_path = file_path
        self.frame_interval = max(1, frame_interval)
        self.max_frames = max_frames
        self.source = VideoFileSource(f"camera_{camera.id}_inspection", file_path)
        self.worker: CaptureWorker | None = None
        self.results: list[dict] = []
        self._stop = False

    def validate_file(self) -> tuple[bool, str | None]:
        """Validate codec/container and size limits."""
        if not os.path.exists(self.file_path):
            return False, "File not found"
        file_size = os.path.getsize(self.file_path)
        max_size = settings.MAX_UPLOAD_VIDEO_BYTES
        if file_size > max_size:
            return False, f"File {file_size} bytes exceeds limit {max_size} bytes"
        # Validate readable by OpenCV
        cap = cv2.VideoCapture(self.file_path)
        if not cap.isOpened():
            return False, "Invalid video codec/container or unreadable"
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if width <= 0 or height <= 0:
            return False, "Invalid resolution"
        if frame_count <= 0:
            return False, "Zero frames in video"
        return True, None

    def run(self) -> VideoInspectionResult:
        """Run inspection on video file."""
        start_time = time.monotonic()
        errors: list[str] = []

        valid, err = self.validate_file()
        if not valid:
            return VideoInspectionResult(
                camera_id=self.camera.id,
                total_frames=0,
                processed_frames=0,
                dropped_frames=0,
                fps=0.0,
                avg_latency_ms=0.0,
                duration_sec=0.0,
                detections=[],
                errors=[err or "Validation failed"],
            )

        self.worker = CaptureWorker(
            self.source,
            buffer_size=1,
            read_timeout=2.0,
            no_frame_timeout=5.0,
            max_reconnects=3,
        )
        self.worker.start()

        frame_idx = 0
        processed = 0

        try:
            while not self._stop:
                if self.max_frames and frame_idx >= self.max_frames:
                    break
                latest = self.worker.get_latest()
                if latest is None:
                    time.sleep(0.01)
                    continue
                ts, frame = latest
                if frame_idx % self.frame_interval == 0:
                    try:
                        result = predict_frame(frame)
                        result["frame_index"] = frame_idx
                        result["timestamp"] = ts
                        self.results.append(result)
                        processed += 1
                    except Exception as exc:
                        errors.append(f"frame {frame_idx}: {exc}")
                frame_idx += 1
                if self.source.frame_count > 0 and frame_idx >= self.source.frame_count:
                    break
        finally:
            self.worker.stop()

        duration = time.monotonic() - start_time
        metrics = self.worker.metrics

        return VideoInspectionResult(
            camera_id=self.camera.id,
            total_frames=frame_idx,
            processed_frames=processed,
            dropped_frames=metrics.frames_dropped,
            fps=metrics.fps,
            avg_latency_ms=metrics.latency_ms,
            duration_sec=duration,
            detections=self.results,
            errors=errors,
        )

    def stop(self) -> None:
        self._stop = True


def inspect_video_file(
    camera: Camera,
    file_path: str,
    frame_interval: int = 1,
    max_frames: int | None = None,
) -> VideoInspectionResult:
    """Convenience function for video inspection."""
    inspector = VideoInspector(camera, file_path, frame_interval, max_frames)
    return inspector.run()