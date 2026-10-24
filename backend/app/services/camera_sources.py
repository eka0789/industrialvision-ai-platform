"""Camera source abstraction for video capture.

Supports simulated, video file, webcam, and RTSP sources.
Credentials are redacted from logs and error messages.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import urlsplit

import cv2
import numpy as np


def redact_rtsp_url(url: str | None) -> str:
    """Redact username/password from RTSP URLs for logging."""
    if not url:
        return "None"
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"rtsp", "rtsps"} and (parsed.username or parsed.password):
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return parsed._replace(netloc=f"[REDACTED]@{host}").geturl()
    return url


class CameraSource(ABC):
    """Abstract camera source interface."""

    def __init__(self, source_id: str):
        self.source_id = source_id
        self.cap: Optional[cv2.VideoCapture] = None
        self._frame_count = 0
        self._error_count = 0

    @abstractmethod
    def open(self) -> bool:
        """Open the source. Return True on success."""
        pass

    def read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read next frame. Return (success, frame)."""
        if not self.cap or not self.cap.isOpened():
            return False, None
        ret, frame = self.cap.read()
        if ret:
            self._frame_count += 1
        else:
            self._error_count += 1
        return ret, frame

    def release(self) -> None:
        """Release resources."""
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.cap = None

    def get_resolution(self) -> tuple[int, int]:
        """Get current resolution (width, height)."""
        if not self.cap:
            return (0, 0)
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (w, h)

    def get_fps(self) -> float:
        """Get source FPS."""
        if not self.cap:
            return 0.0
        return float(self.cap.get(cv2.CAP_PROP_FPS))

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def error_count(self) -> int:
        return self._error_count


class SimulatedSource(CameraSource):
    """Simulated source: generates test frames."""

    def open(self) -> bool:
        # ponytail: real simulated source would generate synthetic frames; for now use webcam fallback
        self.cap = cv2.VideoCapture(0)
        return self.cap.isOpened()


class VideoFileSource(CameraSource):
    """Video file source."""

    def __init__(self, source_id: str, file_path: str):
        super().__init__(source_id)
        self.file_path = file_path

    def open(self) -> bool:
        self.cap = cv2.VideoCapture(self.file_path)
        return self.cap.isOpened()


class WebcamSource(CameraSource):
    """USB webcam source."""

    def __init__(self, source_id: str, device_index: int = 0):
        super().__init__(source_id)
        self.device_index = device_index

    def open(self) -> bool:
        self.cap = cv2.VideoCapture(self.device_index)
        return self.cap.isOpened()


class RTSPSource(CameraSource):
    """RTSP stream source."""

    def __init__(self, source_id: str, rtsp_url: str):
        super().__init__(source_id)
        self.rtsp_url = rtsp_url

    def open(self) -> bool:
        self.cap = cv2.VideoCapture(self.rtsp_url)
        opened = self.cap.isOpened()
        if not opened:
            # Redact credentials in error context
            safe_url = redact_rtsp_url(self.rtsp_url)
            raise ConnectionError(f"Cannot open RTSP source: {safe_url}")
        return opened


def create_camera_source(source_type: str, source_url: str | None, camera_id: int) -> CameraSource:
    """Factory to create camera source by type."""
    source_id = f"camera_{camera_id}"

    if source_type == "simulated":
        return SimulatedSource(source_id)
    elif source_type == "local_file":
        if not source_url:
            raise ValueError("local_file source requires source_url")
        return VideoFileSource(source_id, source_url)
    elif source_type == "webcam":
        device_index = int(source_url) if source_url and source_url.isdigit() else 0
        return WebcamSource(source_id, device_index)
    elif source_type == "rtsp":
        if not source_url:
            raise ValueError("rtsp source requires source_url")
        return RTSPSource(source_id, source_url)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")
