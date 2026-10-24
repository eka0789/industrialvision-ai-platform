"""Phase 2 camera/source/worker tests."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from app.services.camera_sources import (
    CameraSource,
    create_camera_source,
    redact_rtsp_url,
)
from app.services.capture_worker import CaptureWorker


class FakeSource(CameraSource):
    def __init__(self, frames_before_failure: int = 2):
        super().__init__("fake")
        self.frames_before_failure = frames_before_failure
        self.opens = 0
        self.reads = 0
        self.cap = MagicMock()
        self.cap.isOpened.return_value = True

    def open(self) -> bool:
        self.opens += 1
        return True

    def read_frame(self):
        self.reads += 1
        if self.reads <= self.frames_before_failure:
            return True, np.zeros((2, 2, 3), dtype=np.uint8)
        return False, None

    def release(self):
        pass


class CameraSourceTests(unittest.TestCase):
    def test_rtsp_credentials_redacted(self):
        safe = redact_rtsp_url("rtsp://user:secret@example.test:554/live")
        self.assertEqual(safe, "rtsp://[REDACTED]@example.test:554/live")
        self.assertNotIn("secret", safe)

    def test_factory_distinguishes_source_types(self):
        self.assertEqual(type(create_camera_source("simulated", None, 1)).__name__, "SimulatedSource")
        self.assertEqual(type(create_camera_source("local_file", "clip.mp4", 1)).__name__, "VideoFileSource")
        self.assertEqual(type(create_camera_source("webcam", "0", 1)).__name__, "WebcamSource")
        self.assertEqual(type(create_camera_source("rtsp", "rtsp://host/live", 1)).__name__, "RTSPSource")


class CaptureWorkerTests(unittest.TestCase):
    def test_latest_frame_buffer_and_graceful_stop(self):
        source = FakeSource(frames_before_failure=100)
        worker = CaptureWorker(source, buffer_size=1, no_frame_timeout=1, max_reconnects=1)
        worker.start()
        deadline = time.time() + 1
        while worker.metrics.frames_read == 0 and time.time() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(worker.get_latest())
        worker.stop()
        self.assertFalse(worker._thread.is_alive())
        self.assertLessEqual(len(worker.buffer), 1)

    def test_reconnects_after_disconnect(self):
        source = FakeSource(frames_before_failure=1)
        worker = CaptureWorker(
            source,
            buffer_size=1,
            no_frame_timeout=0.01,
            max_reconnects=2,
            backoff_base=0.001,
        )
        worker.start()
        deadline = time.time() + 1
        while worker.metrics.reconnects == 0 and time.time() < deadline:
            time.sleep(0.01)
        worker.stop()
        self.assertGreaterEqual(worker.metrics.reconnects, 1)


if __name__ == "__main__":
    unittest.main()
