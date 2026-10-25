"""T22 runtime metrics collector tests: windows, units, resources, wiring."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from app.services.camera_sources import CameraSource
from app.services.capture_worker import CaptureWorker
from app.services.runtime_metrics import (
    ERROR_MESSAGE_LIMIT,
    LATENCY_STAGES,
    MIN_PERCENTILE_SAMPLES,
    UNITS,
    ResourceSampler,
    RuntimeMetricsCollector,
    collect_hardware,
    get_runtime_metrics,
    reset_runtime_metrics,
)

LINUX = sys.platform.startswith("linux")
PROCFS = LINUX and Path("/proc/stat").is_file() and Path("/proc/meminfo").is_file()


def leaf_paths(obj, prefix: str = "") -> list[str]:
    """All leaf dotted paths in a nested snapshot."""
    paths: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            paths.extend(leaf_paths(value, f"{prefix}{key}."))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            paths.extend(leaf_paths(value, f"{prefix}{index}."))
    else:
        paths.append(prefix.rstrip("."))
    return paths


def numeric_leaf_paths(obj, prefix: str = "") -> list[str]:
    """Dotted paths of every numeric (non-boolean) leaf in a snapshot."""
    paths: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            paths.extend(numeric_leaf_paths(value, f"{prefix}{key}."))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            paths.extend(numeric_leaf_paths(value, f"{prefix}{index}."))
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        paths.append(prefix.rstrip("."))
    return paths


class FakeSource(CameraSource):
    """Minimal source: one good frame, then perpetual read failures."""

    def __init__(self, frames_before_failure: int = 1):
        super().__init__("fake")
        self.frames_before_failure = frames_before_failure
        self.reads = 0
        self.cap = MagicMock()
        self.cap.isOpened.return_value = True

    def open(self) -> bool:
        return True

    def read_frame(self):
        self.reads += 1
        if self.reads <= self.frames_before_failure:
            return True, np.zeros((2, 2, 3), dtype=np.uint8)
        return False, None

    def release(self):
        pass


class LatencyTests(unittest.TestCase):
    def test_stats_on_known_samples(self):
        collector = RuntimeMetricsCollector()
        for value in range(1, 101):
            collector.record_latency("inference", float(value))
        stats = collector.snapshot()["latency"]["inference"]
        self.assertEqual(stats["count"], 100)
        self.assertEqual(stats["mean"], 50.5)
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["max"], 100.0)
        self.assertEqual(stats["last"], 100.0)
        self.assertAlmostEqual(stats["p50"], 50.5, places=6)
        self.assertAlmostEqual(stats["p95"], 95.05, places=6)
        self.assertAlmostEqual(stats["p99"], 99.01, places=6)

    def test_window_trims_to_capacity(self):
        collector = RuntimeMetricsCollector(latency_window_samples=5)
        for value in range(1, 11):
            collector.record_latency("capture", float(value))
        snapshot = collector.snapshot()
        stats = snapshot["latency"]["capture"]
        self.assertEqual(snapshot["window"]["latency_window_samples"], 5)
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["min"], 6.0)
        self.assertEqual(stats["max"], 10.0)
        self.assertEqual(stats["last"], 10.0)

    def test_percentiles_hidden_until_enough_samples(self):
        collector = RuntimeMetricsCollector()
        for value in (1.0, 2.0, 3.0):
            collector.record_latency("inference", value)
        stats = collector.snapshot()["latency"]["inference"]
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["mean"], 2.0)
        self.assertIsNone(stats["p50"])
        self.assertIsNone(stats["p95"])
        self.assertIsNone(stats["p99"])
        self.assertLess(3, MIN_PERCENTILE_SAMPLES)

    def test_stages_are_independent(self):
        collector = RuntimeMetricsCollector()
        collector.record_latency("capture", 5.0)
        collector.record_latency("inference", 10.0)
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["latency"]["capture"]["last"], 5.0)
        self.assertEqual(snapshot["latency"]["inference"]["last"], 10.0)
        self.assertEqual(set(snapshot["latency"]), set(LATENCY_STAGES))

    def test_rejects_invalid_latency(self):
        collector = RuntimeMetricsCollector()
        with self.assertRaises(ValueError):
            collector.record_latency("inference", -0.5)
        with self.assertRaises(ValueError):
            collector.record_latency("inference", float("nan"))
        with self.assertRaises(ValueError):
            collector.record_latency("inference", float("inf"))
        with self.assertRaises(ValueError):
            collector.record_latency("inference", "not-a-number")
        with self.assertRaises(ValueError):
            collector.record_latency("unknown-stage", 1.0)
        collector.record_latency("inference", 0.0)  # zero is valid
        self.assertEqual(collector.snapshot()["latency"]["inference"]["count"], 1)


class FpsWindowTests(unittest.TestCase):
    def test_fps_uses_sliding_window(self):
        now = [0.0]
        collector = RuntimeMetricsCollector(
            fps_window_seconds=30.0,
            clock=lambda: now[0],
            wall_clock=lambda: 1_700_000_000.0 + now[0],
        )
        for _ in range(10):
            collector.record_frame()

        # Elapsed time of zero must not divide by zero.
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["fps"]["value"], 0.0)
        self.assertEqual(snapshot["fps"]["frames_in_window"], 10)

        now[0] = 10.0  # 10 frames in 10 seconds
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["fps"]["frames_in_window"], 10)
        self.assertEqual(snapshot["fps"]["effective_window_sec"], 10.0)
        self.assertAlmostEqual(snapshot["fps"]["value"], 1.0, places=6)

        now[0] = 41.0  # beyond the 30s window: old frames pruned
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["fps"]["frames_in_window"], 0)
        self.assertEqual(snapshot["fps"]["value"], 0.0)

        for _ in range(6):
            collector.record_frame()
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["fps"]["frames_in_window"], 6)
        self.assertEqual(snapshot["fps"]["effective_window_sec"], 30.0)
        self.assertAlmostEqual(snapshot["fps"]["value"], 0.2, places=6)

    def test_invalid_window_configuration_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeMetricsCollector(fps_window_seconds=0)
        with self.assertRaises(ValueError):
            RuntimeMetricsCollector(latency_window_samples=0)
        with self.assertRaises(ValueError):
            RuntimeMetricsCollector(recent_error_limit=-1)


class CounterTests(unittest.TestCase):
    def test_counters_and_rates(self):
        collector = RuntimeMetricsCollector()
        for _ in range(8):
            collector.record_frame()
        for _ in range(2):
            collector.record_dropped_frame()
        for _ in range(3):
            collector.record_reconnect()
        for value in (1.0, 2.0, 3.0, 4.0):
            collector.record_latency("inference", value)
        collector.record_inference_error("boom")

        snapshot = collector.snapshot()
        self.assertEqual(
            snapshot["counters"],
            {
                "frames_processed": 8,
                "frames_dropped": 2,
                "reconnects": 3,
                "inference_completed": 4,
                "inference_errors": 1,
            },
        )
        self.assertAlmostEqual(snapshot["rates"]["frame_drop_rate"], 2 / 10, places=6)
        self.assertAlmostEqual(snapshot["rates"]["inference_error_rate"], 1 / 5, places=6)
        self.assertEqual(snapshot["latency"]["inference"]["count"], 4)

    def test_rates_are_zero_without_samples(self):
        snapshot = RuntimeMetricsCollector().snapshot()
        self.assertEqual(snapshot["rates"]["frame_drop_rate"], 0.0)
        self.assertEqual(snapshot["rates"]["inference_error_rate"], 0.0)
        self.assertEqual(snapshot["fps"]["value"], 0.0)

    def test_error_log_bounded_and_truncated(self):
        collector = RuntimeMetricsCollector()
        for index in range(15):
            collector.record_inference_error(f"{index}-" + "x" * 1000)
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["counters"]["inference_errors"], 15)
        self.assertEqual(len(snapshot["recent_errors"]), 10)
        for entry in snapshot["recent_errors"]:
            self.assertLessEqual(len(entry["message"]), ERROR_MESSAGE_LIMIT)
            self.assertTrue(entry["message"].endswith("..."))
        self.assertTrue(snapshot["recent_errors"][-1]["message"].startswith("14-"))

    def test_error_limit_zero_still_counts(self):
        collector = RuntimeMetricsCollector(recent_error_limit=0)
        collector.record_inference_error("boom")
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["counters"]["inference_errors"], 1)
        self.assertEqual(snapshot["recent_errors"], [])


class SnapshotShapeTests(unittest.TestCase):
    def _populated(self) -> RuntimeMetricsCollector:
        collector = RuntimeMetricsCollector()
        for _ in range(3):
            collector.record_frame()
        collector.record_dropped_frame()
        collector.record_reconnect()
        collector.record_latency("capture", 4.0)
        for value in range(1, 11):
            collector.record_latency("inference", float(value))
        collector.record_inference_error("sample failure")
        return collector

    def test_units_cover_every_numeric_metric(self):
        snapshot = self._populated().snapshot()
        numeric = numeric_leaf_paths(snapshot)
        self.assertTrue(numeric)
        missing = [path for path in numeric if path not in snapshot["units"]]
        self.assertEqual(missing, [])

    def test_every_declared_unit_maps_to_a_snapshot_field(self):
        snapshot = self._populated().snapshot()
        present = set(leaf_paths(snapshot))
        missing = [key for key in UNITS if key not in present]
        self.assertEqual(missing, [])
        for key, unit in snapshot["units"].items():
            self.assertIsInstance(unit, str)
            self.assertTrue(unit, f"empty unit for {key}")

    def test_unit_values_are_explicit(self):
        self.assertEqual(UNITS["latency.inference.mean"], "ms")
        self.assertEqual(UNITS["fps.value"], "frames/second")
        self.assertEqual(UNITS["counters.frames_dropped"], "frames")
        self.assertEqual(UNITS["resources.memory.used_bytes"], "bytes")
        self.assertEqual(UNITS["rates.inference_error_rate"], "ratio")

    def test_snapshot_is_json_serializable(self):
        snapshot = self._populated().snapshot()
        encoded = json.dumps(snapshot)
        self.assertEqual(json.loads(encoded)["counters"], snapshot["counters"])
        self.assertIn("collected_at", json.loads(encoded))

    def test_window_metadata_is_reported(self):
        snapshot = self._populated().snapshot()
        window = snapshot["window"]
        self.assertIn("T", window["started_at"])
        self.assertGreaterEqual(window["elapsed_sec"], 0.0)
        self.assertEqual(window["fps_window_seconds"], 30.0)
        self.assertEqual(window["latency_window_samples"], 1000)

    def test_hardware_metadata(self):
        hardware = self._populated().snapshot()["hardware"]
        self.assertEqual(hardware["python_version"], platform.python_version())
        self.assertEqual(hardware["cpu_count"], os.cpu_count())
        self.assertTrue(hardware["platform"])
        self.assertTrue(hardware["machine"])
        self.assertTrue(
            hardware["cpu_model"] is None or isinstance(hardware["cpu_model"], str)
        )
        if LINUX:
            self.assertIsInstance(hardware["total_ram_bytes"], int)
            self.assertGreater(hardware["total_ram_bytes"], 0)
            self.assertIsInstance(hardware["cpu_cores_physical"], int)
            self.assertGreaterEqual(hardware["cpu_cores_physical"], 1)

    def test_gpu_availability_is_explicit(self):
        gpu = self._populated().snapshot()["gpu"]
        self.assertIsInstance(gpu["available"], bool)
        if gpu["available"]:
            self.assertIsInstance(gpu["name"], str)
            self.assertGreater(gpu["total_memory_bytes"], 0)
        else:
            self.assertIsNone(gpu["name"])
            self.assertIsNone(gpu["total_memory_bytes"])
            self.assertIsNone(gpu["utilization_percent"])
            self.assertIsNone(gpu["source"])

    def test_gpu_snapshot_is_used_when_available(self):
        stub = {
            "available": True,
            "source": "pynvml",
            "vendor": "NVIDIA",
            "name": "Test GPU",
            "total_memory_bytes": 8 * 1024**3,
            "used_memory_bytes": 1024**3,
            "utilization_percent": 55.0,
        }
        with patch("app.services.runtime_metrics.gpu_snapshot", return_value=stub):
            snapshot = RuntimeMetricsCollector().snapshot()
        self.assertEqual(snapshot["gpu"], stub)
        self.assertIn("gpu.utilization_percent", snapshot["units"])


class ResourceSamplerTests(unittest.TestCase):
    def test_auto_backend_resolves(self):
        sampler = ResourceSampler()
        self.assertIn(sampler.backend, ("psutil", "procfs", "unavailable"))
        if LINUX:
            self.assertIn(sampler.backend, ("psutil", "procfs"))

    @unittest.skipUnless(PROCFS, "requires Linux /proc")
    def test_procfs_cpu_and_memory(self):
        sampler = ResourceSampler(backend="procfs")
        time.sleep(0.1)
        cpu = sampler.cpu_percent()
        self.assertIsNotNone(cpu)
        self.assertGreaterEqual(cpu, 0.0)
        self.assertLessEqual(cpu, 100.0)

        memory = sampler.memory()
        self.assertEqual(memory["source"], "procfs")
        self.assertGreater(memory["total_bytes"], 0)
        self.assertLessEqual(memory["used_bytes"], memory["total_bytes"])
        self.assertGreaterEqual(memory["percent"], 0.0)
        self.assertLessEqual(memory["percent"], 100.0)
        self.assertGreater(memory["process_rss_bytes"], 0)

    @unittest.skipUnless(PROCFS, "requires Linux /proc")
    def test_procfs_returns_none_without_elapsed_time(self):
        now = [100.0]  # frozen clock: no time passes between samples
        sampler = ResourceSampler(backend="procfs", clock=lambda: now[0])
        self.assertIsNone(sampler.cpu_percent())

    def test_psutil_backend_with_stub_module(self):
        stub = MagicMock()
        stub.cpu_percent.return_value = 42.5
        stub.virtual_memory.return_value = MagicMock(
            total=8 * 1024**3, available=2 * 1024**3, percent=75.0
        )
        stub.Process.return_value.memory_info.return_value = MagicMock(rss=123 * 1024**2)
        with patch("app.services.runtime_metrics._import_psutil", return_value=stub):
            sampler = ResourceSampler(backend="auto")
            self.assertEqual(sampler.backend, "psutil")
            self.assertEqual(sampler.cpu_percent(), 42.5)
            memory = sampler.memory()
        self.assertEqual(memory["source"], "psutil")
        self.assertEqual(memory["total_bytes"], 8 * 1024**3)
        self.assertEqual(memory["used_bytes"], 6 * 1024**3)
        self.assertEqual(memory["percent"], 75.0)
        self.assertEqual(memory["process_rss_bytes"], 123 * 1024**2)

    def test_explicit_psutil_backend_requires_the_library(self):
        with patch("app.services.runtime_metrics._import_psutil", return_value=None):
            with self.assertRaises(ValueError):
                ResourceSampler(backend="psutil")

    @unittest.skipUnless(PROCFS, "requires Linux /proc")
    def test_explicit_procfs_backend(self):
        self.assertEqual(ResourceSampler(backend="procfs").backend, "procfs")

    def test_unavailable_backend_reports_no_numbers(self):
        sampler = ResourceSampler(backend="unavailable")
        self.assertEqual(sampler.backend, "unavailable")
        self.assertIsNone(sampler.cpu_percent())
        memory = sampler.memory()
        self.assertEqual(
            memory,
            {
                "used_bytes": None,
                "total_bytes": None,
                "percent": None,
                "process_rss_bytes": None,
                "source": "unavailable",
            },
        )

    def test_auto_degrades_when_no_source_available(self):
        with patch("app.services.runtime_metrics._import_psutil", return_value=None), patch(
            "app.services.runtime_metrics._procfs_available", return_value=False
        ):
            self.assertEqual(ResourceSampler(backend="auto").backend, "unavailable")

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            ResourceSampler(backend="magic")

    @unittest.skipUnless(PROCFS, "requires Linux /proc")
    def test_hardware_falls_back_to_procfs(self):
        with patch("app.services.runtime_metrics._import_psutil", return_value=None):
            hardware = collect_hardware()
        self.assertIsInstance(hardware["total_ram_bytes"], int)
        self.assertGreater(hardware["total_ram_bytes"], 0)
        self.assertIsInstance(hardware["cpu_cores_physical"], int)
        self.assertGreaterEqual(hardware["cpu_cores_physical"], 1)
        self.assertTrue(hardware["cpu_model"] is None or isinstance(hardware["cpu_model"], str))


class ThreadingTests(unittest.TestCase):
    def test_concurrent_recording_is_consistent(self):
        collector = RuntimeMetricsCollector()
        failures: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(250):
                    collector.record_latency("inference", 1.0)
                    collector.record_frame()
            except BaseException as exc:  # pragma: no cover - failure reporting
                failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(failures, [])
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["counters"]["inference_completed"], 1000)
        self.assertEqual(snapshot["counters"]["frames_processed"], 1000)
        self.assertEqual(snapshot["latency"]["inference"]["count"], 1000)


class SingletonTests(unittest.TestCase):
    def test_singleton_and_reset(self):
        first = get_runtime_metrics()
        self.assertIs(first, get_runtime_metrics())
        first.record_reconnect()
        self.assertEqual(first.snapshot()["counters"]["reconnects"], 1)

        fresh = reset_runtime_metrics()
        self.assertIsNot(fresh, first)
        self.assertIs(fresh, get_runtime_metrics())
        self.assertEqual(fresh.snapshot()["counters"]["reconnects"], 0)


class CaptureWorkerWiringTests(unittest.TestCase):
    def test_worker_records_frames_latency_and_reconnects(self):
        collector = RuntimeMetricsCollector()
        worker = CaptureWorker(
            FakeSource(frames_before_failure=1),
            buffer_size=1,
            no_frame_timeout=0.01,
            max_reconnects=1,
            backoff_base=0.001,
            collector=collector,
        )
        worker.start()
        deadline = time.time() + 2
        snapshot = collector.snapshot()
        try:
            while time.time() < deadline:
                snapshot = collector.snapshot()
                if (
                    snapshot["counters"]["frames_processed"] >= 1
                    and snapshot["counters"]["reconnects"] >= 1
                ):
                    break
                time.sleep(0.01)
        finally:
            worker.stop()

        self.assertGreaterEqual(snapshot["counters"]["frames_processed"], 1)
        self.assertGreaterEqual(snapshot["counters"]["reconnects"], 1)
        capture = snapshot["latency"]["capture"]
        self.assertGreaterEqual(capture["count"], 1)
        self.assertGreaterEqual(capture["min"], 0.0)
        self.assertGreater(snapshot["fps"]["value"], 0.0)

    def test_worker_without_collector_is_unchanged(self):
        worker = CaptureWorker(FakeSource(), buffer_size=1, no_frame_timeout=1, max_reconnects=1)
        self.assertIsNone(worker.collector)
        worker.start()
        deadline = time.time() + 1
        while worker.metrics.frames_read == 0 and time.time() < deadline:
            time.sleep(0.01)
        worker.stop()


class VideoInspectionWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="metrics-video-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "clip.avi")
        writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (32, 32))
        if not writer.isOpened():
            self.skipTest("OpenCV MJPG writer unavailable")
        for _ in range(10):
            writer.write(np.zeros((32, 32, 3), dtype=np.uint8))
        writer.release()

        from app.db.models import Camera

        self.camera = Camera(name="metrics-cam", source_type="local_file")

    def _run(self, predict_result=None, predict_error=None):
        from app.services import video_inspection
        from app.services.video_inspection import VideoInspector

        inspector = VideoInspector(self.camera, self.path, frame_interval=1, max_frames=4)
        watchdog = threading.Timer(20.0, inspector.stop)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        kwargs = (
            {"side_effect": predict_error}
            if predict_error is not None
            else {"return_value": predict_result}
        )
        with patch.object(video_inspection, "predict_frame", **kwargs):
            result = inspector.run()
        return result

    def test_reported_metrics_carry_units_windows_and_hardware(self):
        result = self._run(predict_result={"detections": [], "latency_ms": 5.0})
        metrics = result.metrics
        self.assertIsNotNone(metrics)
        json.dumps(metrics)  # machine-readable

        self.assertEqual(metrics["latency"]["inference"]["last"], 5.0)
        self.assertGreaterEqual(metrics["latency"]["capture"]["count"], 1)
        self.assertGreaterEqual(metrics["counters"]["frames_processed"], 1)
        self.assertEqual(metrics["counters"]["inference_errors"], 0)

        missing = [path for path in numeric_leaf_paths(metrics) if path not in metrics["units"]]
        self.assertEqual(missing, [])
        self.assertTrue(metrics["window"]["started_at"])
        self.assertTrue(metrics["hardware"]["platform"])
        self.assertIn(metrics["resources"]["backend"], ("psutil", "procfs", "unavailable"))

    def test_inference_failures_are_counted_not_hidden(self):
        result = self._run(predict_error=RuntimeError("model unavailable"))
        self.assertTrue(result.errors)
        metrics = result.metrics
        self.assertGreaterEqual(metrics["counters"]["inference_errors"], 1)
        self.assertEqual(metrics["latency"]["inference"]["count"], 0)
        self.assertGreater(metrics["rates"]["inference_error_rate"], 0.0)
        self.assertTrue(metrics["recent_errors"])
        self.assertIn("model unavailable", metrics["recent_errors"][-1]["message"])


if __name__ == "__main__":
    unittest.main()
