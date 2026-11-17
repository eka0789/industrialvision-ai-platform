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
    """Simulated source: generates deterministic synthetic frames without requiring a webcam."""

    def __init__(self, source_id: str, width: int = 640, height: int = 480):
        super().__init__(source_id)
        self.width = width
        self.height = height
        self._opened = False

    def open(self) -> bool:
        self._opened = True
        return True

    def read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        if not self._opened:
            return False, None
        frame = np.full((self.height, self.width, 3), 36, dtype=np.uint8)
        # Draw a moving calibration bar so downstream frame/blur checks see real structure
        x = (self._frame_count * 12) % max(1, self.width - 80)
        cv2.rectangle(frame, (x, 120), (x + 80, 360), (190, 210, 180), -1)
        cv2.putText(
            frame,
            f"SIM {self.source_id} #{self._frame_count}",
            (24, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (220, 245, 220),
            2,
        )
        self._frame_count += 1
        return True, frame

    def release(self) -> None:
        self._opened = False

    def get_resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    def get_fps(self) -> float:
        return 15.0


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


class GenICamSource(CameraSource):
    """Industrial GigE Vision / USB3 Vision camera via GenICam (harvesters + GenTL .cti).

    source_url format: ``genicam:///path/to/producer.cti`` or
    ``genicam:///path/to/producer.cti?id=<serial_or_index>`` (or a plain ``.cti`` path).
    """

    def __init__(self, source_id: str, source_url: str):
        super().__init__(source_id)
        self.source_url = source_url
        self._harvester = None
        self._ia = None
        self._width = 0
        self._height = 0

    def _parse_url(self) -> tuple[str, str | None]:
        raw = self.source_url
        device_id: str | None = None
        if raw.startswith("genicam://"):
            parsed = urlsplit(raw)
            cti_path = parsed.path or parsed.netloc
            if parsed.query:
                for part in parsed.query.split("&"):
                    if part.startswith("id="):
                        device_id = part[3:] or None
            return cti_path, device_id
        return raw, None

    def open(self) -> bool:
        try:
            from harvesters.core import Harvester  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "GenICam support requires `harvesters` (install backend/requirements-industrial.txt) "
                "and a vendor GenTL (.cti) producer"
            ) from exc

        cti_path, device_id = self._parse_url()
        if not cti_path:
            raise ValueError("GenICam source requires a GenTL .cti producer path in source_url")

        h = Harvester()
        h.add_file(cti_path)
        h.update()
        if not h.device_info_list:
            h.reset()
            raise ConnectionError(f"No GenICam devices discovered via producer: {cti_path}")

        try:
            if device_id is not None and device_id.isdigit():
                ia = h.create(int(device_id))
            elif device_id is not None:
                ia = h.create({"serial_number": device_id})
            else:
                ia = h.create(0)
            ia.start()
        except Exception as exc:
            h.reset()
            raise ConnectionError(f"Cannot open GenICam device ({device_id or 0}): {exc}") from exc

        self._harvester = h
        self._ia = ia
        return True

    def read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        if self._ia is None:
            return False, None
        try:
            with self._ia.fetch(timeout=2.0) as buffer:
                component = buffer.payload.components[0]
                arr = component.data.reshape(component.height, component.width, -1)
                if arr.shape[2] == 1:
                    frame = cv2.cvtColor(arr[:, :, 0], cv2.COLOR_GRAY2BGR)
                else:
                    frame = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
                self._height, self._width = frame.shape[:2]
                self._frame_count += 1
                return True, frame
        except Exception:
            self._error_count += 1
            return False, None

    def release(self) -> None:
        if self._ia is not None:
            try:
                self._ia.stop()
                self._ia.destroy()
            except Exception:
                pass
            self._ia = None
        if self._harvester is not None:
            try:
                self._harvester.reset()
            except Exception:
                pass
            self._harvester = None

    def get_resolution(self) -> tuple[int, int]:
        return (self._width, self._height)


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
    elif source_type == "genicam":
        if not source_url:
            raise ValueError("genicam source requires a .cti producer path in source_url")
        return GenICamSource(source_id, source_url)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")
