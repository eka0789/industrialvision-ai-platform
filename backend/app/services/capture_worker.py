"""Bounded capture worker with reconnect/backoff and metrics."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.services.camera_sources import CameraSource
from app.services.runtime_metrics import RuntimeMetricsCollector


@dataclass
class CaptureMetrics:
    frames_read: int = 0
    frames_dropped: int = 0
    reconnects: int = 0
    read_errors: int = 0
    last_frame_at: float | None = None
    started_at: float | None = None
    latency_ms: float = 0.0

    @property
    def fps(self) -> float:
        if not self.started_at or not self.last_frame_at:
            return 0.0
        elapsed = self.last_frame_at - self.started_at
        return self.frames_read / elapsed if elapsed > 0 else 0.0


class CaptureWorker:
    """Reads source in background, keeping only latest bounded frames."""

    _active_workers = 0
    _active_lock = threading.Lock()

    def __init__(
        self,
        source: CameraSource,
        buffer_size: int = 1,
        read_timeout: float = 2.0,
        no_frame_timeout: float = 5.0,
        max_reconnects: int = 5,
        backoff_base: float = 0.2,
        connection_limit: int = 16,
        collector: RuntimeMetricsCollector | None = None,
    ):
        self.source = source
        self.collector = collector
        self.buffer_size = max(1, buffer_size)
        self.read_timeout = read_timeout
        self.no_frame_timeout = no_frame_timeout
        self.max_reconnects = max_reconnects
        self.backoff_base = backoff_base
        self.connection_limit = connection_limit
        self.buffer: deque[tuple[float, np.ndarray]] = deque(maxlen=self.buffer_size)
        self.metrics = CaptureMetrics()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def start(self) -> None:
        with self._active_lock:
            if self._active_workers >= self.connection_limit:
                raise RuntimeError("Capture connection limit reached")
            self._active_workers += 1
        self.metrics.started_at = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self.source.release()
        with self._active_lock:
            self._active_workers = max(0, self._active_workers - 1)

    def get_latest(self) -> tuple[float, np.ndarray] | None:
        with self._lock:
            return self.buffer[-1] if self.buffer else None

    def _run(self) -> None:
        reconnects = 0
        last_frame = time.monotonic()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    if not self.source.cap or not self.source.cap.isOpened():
                        if not self.source.open():
                            raise ConnectionError("source open failed")
                    ok, frame = self.source.read_frame()
                    elapsed = time.monotonic() - started
                    self.metrics.latency_ms = elapsed * 1000
                    if elapsed > self.read_timeout or not ok or frame is None:
                        self.metrics.read_errors += 1
                        if time.monotonic() - last_frame >= self.no_frame_timeout:
                            raise TimeoutError("no frame received within timeout")
                        time.sleep(0.01)
                        continue
                    if self.collector is not None:
                        self.collector.record_latency("capture", elapsed * 1000)
                    reconnects = 0
                    timestamp = time.time()
                    with self._lock:
                        if len(self.buffer) == self.buffer.maxlen:
                            self.metrics.frames_dropped += 1
                            if self.collector is not None:
                                self.collector.record_dropped_frame()
                        self.buffer.append((timestamp, frame))
                    self.metrics.frames_read += 1
                    if self.collector is not None:
                        self.collector.record_frame()
                    self.metrics.last_frame_at = time.monotonic()
                    last_frame = time.monotonic()
                except Exception as exc:
                    self.last_error = str(exc)
                    self.source.release()
                    if reconnects >= self.max_reconnects:
                        break
                    delay = min(self.backoff_base * (2**reconnects), 10.0)
                    reconnects += 1
                    self.metrics.reconnects += 1
                    if self.collector is not None:
                        self.collector.record_reconnect()
                    self._stop.wait(delay)
        finally:
            self.source.release()
