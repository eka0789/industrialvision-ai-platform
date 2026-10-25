"""T23 benchmark runner and CLI gate tests (no fabricated numbers)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.benchmark.cli import main as cli_main
from app.benchmark.cli import resolve_model_artifact
from app.benchmark.runner import (
    BENCHMARK_REPORT_VERSION,
    BenchmarkError,
    load_input,
    run_benchmark,
    sha256_array,
    summarize,
    write_report,
)
from app.db.models import ModelVersion
from tests.test_dataset_validation import write_image

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class StubEngine:
    """Predict callable returning the stable dict wire shape."""

    def __init__(self, detections: list | None = None, fail_at: int | None = None, mutate: bool = False):
        self.calls = 0
        self.detections = detections if detections is not None else []
        self.fail_at = fail_at
        self.mutate = mutate

    def predict(self, image):
        self.calls += 1
        if self.fail_at is not None and self.calls == self.fail_at:
            raise RuntimeError("model unavailable")
        if self.mutate:
            image[0, 0, 0] = (int(image[0, 0, 0]) + 1) % 256
        return {"detections": list(self.detections), "latency_ms": 1.0}


class SummarizeTests(unittest.TestCase):
    def test_stats_reported_at_threshold(self):
        samples = [float(value) for value in range(1, 31)]
        measurements = summarize(samples, min_samples=30)
        self.assertEqual(measurements["unit"], "ms")
        self.assertEqual(measurements["sample_count"], 30)
        self.assertEqual(measurements["min_ms"], 1.0)
        self.assertEqual(measurements["max_ms"], 30.0)
        self.assertEqual(measurements["samples_ms"], samples)
        self.assertIsNone(measurements["stats_withheld_reason"])
        stats = measurements["stats"]
        self.assertEqual(stats["average_ms"], 15.5)
        self.assertAlmostEqual(stats["p50_ms"], 15.5, places=6)
        self.assertAlmostEqual(stats["p95_ms"], 28.55, places=6)
        self.assertAlmostEqual(stats["p99_ms"], 29.71, places=6)

    def test_stats_withheld_below_threshold(self):
        measurements = summarize([1.0, 2.0, 3.0, 4.0, 5.0], min_samples=30)
        self.assertIsNone(measurements["stats"])
        self.assertIn("fewer than 30 samples", measurements["stats_withheld_reason"])
        self.assertIn("collected 5", measurements["stats_withheld_reason"])
        # Observed facts are still reported honestly.
        self.assertEqual(measurements["sample_count"], 5)
        self.assertEqual(measurements["min_ms"], 1.0)
        self.assertEqual(measurements["max_ms"], 5.0)
        self.assertEqual(len(measurements["samples_ms"]), 5)

    def test_one_below_threshold_is_enough_to_withhold(self):
        samples = [float(value) for value in range(1, 30)]
        self.assertIsNone(summarize(samples, min_samples=30)["stats"])
        self.assertIsNotNone(summarize(samples + [30.0], min_samples=30)["stats"])

    def test_empty_samples_report_no_stats(self):
        measurements = summarize([], min_samples=30)
        self.assertEqual(measurements["sample_count"], 0)
        self.assertIsNone(measurements["min_ms"])
        self.assertIsNone(measurements["max_ms"])
        self.assertIsNone(measurements["stats"])
        self.assertIn("no samples", measurements["stats_withheld_reason"])

    def test_invalid_min_samples_rejected(self):
        with self.assertRaises(BenchmarkError):
            summarize([1.0], min_samples=0)


class LoadInputTests(unittest.TestCase):
    def test_synthetic_input_is_deterministic_and_labeled(self):
        first, first_meta = load_input(seed=42, width=16, height=8)
        second, second_meta = load_input(seed=42, width=16, height=8)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertEqual(first_meta, second_meta)
        self.assertEqual(first_meta["source"], "synthetic")
        self.assertEqual(first_meta["seed"], 42)
        self.assertEqual((first_meta["width"], first_meta["height"]), (16, 8))
        self.assertEqual(first_meta["channels"], 3)
        self.assertEqual(first_meta["dtype"], "uint8")
        self.assertEqual(first_meta["sha256"], sha256_array(first))

    def test_different_seeds_differ(self):
        _, first = load_input(seed=1, width=8, height=8)
        _, second = load_input(seed=2, width=8, height=8)
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_file_input_records_decoded_pixel_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_image(root, "in.png")
            array, meta = load_input(path=root / "images" / "in.png")
        self.assertEqual(meta["source"], "file")
        self.assertEqual(meta["sha256"], sha256_array(array))
        self.assertEqual(meta["width"], array.shape[1])
        self.assertEqual(meta["height"], array.shape[0])

    def test_undecodable_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.png"
            broken.write_bytes(b"definitely not a png")
            with self.assertRaises(BenchmarkError):
                load_input(path=broken)

    def test_exactly_one_input_source_required(self):
        with self.assertRaises(BenchmarkError):
            load_input()
        with self.assertRaises(BenchmarkError):
            load_input(path="x.png", seed=1)


class RunBenchmarkTests(unittest.TestCase):
    def _input(self, seed: int = 7):
        return load_input(seed=seed, width=8, height=8)

    def test_warmup_excluded_and_input_identity_holds(self):
        array, meta = self._input()
        engine = StubEngine()
        report = run_benchmark(
            engine.predict,
            input_array=array,
            input_meta=meta,
            iterations=7,
            warmup=3,
            min_samples=5,
            engine_meta={"framework": "stub"},
            environment={},
        )
        self.assertEqual(engine.calls, 10)  # 3 warmup + 7 measured
        self.assertEqual(report["protocol"]["warmup_iterations"], 3)
        self.assertEqual(report["protocol"]["iterations"], 7)
        self.assertEqual(report["measurements"]["sample_count"], 7)
        # Identical input verified before and after the run.
        self.assertEqual(meta["sha256"], sha256_array(array))

    def test_stats_gated_by_min_samples(self):
        array, meta = self._input()
        report = run_benchmark(
            StubEngine().predict,
            input_array=array,
            input_meta=meta,
            iterations=5,
            warmup=1,
            min_samples=30,
            environment={},
        )
        self.assertEqual(report["status"], "completed")
        self.assertIsNone(report["measurements"]["stats"])
        self.assertIn("fewer than 30 samples", report["measurements"]["stats_withheld_reason"])

    def test_mutation_of_input_fails_the_run(self):
        array, meta = self._input()
        with self.assertRaises(BenchmarkError) as ctx:
            run_benchmark(
                StubEngine(mutate=True).predict,
                input_array=array,
                input_meta=meta,
                iterations=3,
                warmup=0,
                environment={},
            )
        self.assertIn("mutated", str(ctx.exception))

    def test_failed_inference_aborts_without_report(self):
        array, meta = self._input()
        engine = StubEngine(fail_at=4)
        with self.assertRaises(BenchmarkError) as ctx:
            run_benchmark(
                engine.predict,
                input_array=array,
                input_meta=meta,
                iterations=5,
                warmup=0,
                environment={},
            )
        self.assertIn("iteration 4 of 5 failed", str(ctx.exception))
        self.assertIn("model unavailable", str(ctx.exception))

    def test_warmup_failure_reports_warmup_stage(self):
        array, meta = self._input()
        engine = StubEngine(fail_at=1)
        with self.assertRaises(BenchmarkError) as ctx:
            run_benchmark(engine.predict, input_array=array, input_meta=meta, iterations=2, warmup=2, environment={})
        self.assertIn("warmup iteration 1 of 2 failed", str(ctx.exception))

    def test_invalid_latency_sample_rejected(self):
        array, meta = self._input()
        times = iter([0.0, -0.01])  # decreasing clock => negative sample
        with self.assertRaises(BenchmarkError) as ctx:
            run_benchmark(
                StubEngine().predict,
                input_array=array,
                input_meta=meta,
                iterations=1,
                warmup=0,
                environment={},
                clock=lambda: next(times),
            )
        self.assertIn("invalid latency sample", str(ctx.exception))

    def test_batch_size_other_than_one_rejected(self):
        array, meta = self._input()
        engine = StubEngine()
        with self.assertRaises(BenchmarkError):
            run_benchmark(
                engine.predict,
                input_array=array,
                input_meta=meta,
                iterations=1,
                warmup=0,
                batch_size=4,
                environment={},
            )
        self.assertEqual(engine.calls, 0)  # refused before any inference

    def test_input_metadata_mismatch_rejected(self):
        array, meta = self._input()
        meta = {**meta, "sha256": "0" * 64}
        with self.assertRaises(BenchmarkError):
            run_benchmark(StubEngine().predict, input_array=array, input_meta=meta, iterations=1, warmup=0, environment={})

    def test_invalid_protocol_arguments_rejected(self):
        array, meta = self._input()
        for kwargs in ({"iterations": 0}, {"warmup": -1}, {"min_samples": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BenchmarkError):
                run_benchmark(
                    StubEngine().predict,
                    input_array=array,
                    input_meta=meta,
                    iterations=kwargs.get("iterations", 1),
                    warmup=kwargs.get("warmup", 0),
                    min_samples=kwargs.get("min_samples", 1),
                    environment={},
                )

    def test_last_inference_from_dict_and_object_shapes(self):
        array, meta = self._input()
        report = run_benchmark(
            lambda frame: {"detections": [{"label": "scratch"}, {"label": "dent"}]},
            input_array=array,
            input_meta=meta,
            iterations=2,
            warmup=0,
            environment={},
        )
        self.assertEqual(report["last_inference"], {"detection_count": 2})

        report = run_benchmark(
            lambda frame: SimpleNamespace(detections=[{"label": "scratch"}]),
            input_array=array,
            input_meta=meta,
            iterations=1,
            warmup=0,
            environment={},
        )
        self.assertEqual(report["last_inference"], {"detection_count": 1})

        report = run_benchmark(
            lambda frame: "unexpected",
            input_array=array,
            input_meta=meta,
            iterations=1,
            warmup=0,
            environment={},
        )
        self.assertIsNone(report["last_inference"])

    def test_report_shape_is_json_serializable(self):
        array, meta = self._input()
        report = run_benchmark(
            StubEngine().predict,
            input_array=array,
            input_meta=meta,
            iterations=3,
            warmup=1,
            min_samples=2,
            engine_meta={"framework": "stub"},
        )
        encoded = json.dumps(report)
        self.assertEqual(json.loads(encoded)["report_version"], BENCHMARK_REPORT_VERSION)
        self.assertEqual(report["status"], "completed")
        datetime.fromisoformat(report["created_at"])  # valid ISO timestamp
        self.assertEqual(report["protocol"]["batch_size"], 1)
        self.assertEqual(report["protocol"]["clock"], "time.perf_counter")
        self.assertEqual(report["measurements"]["unit"], "ms")
        self.assertTrue(report["environment"])  # hardware metadata filled by default


class WriteReportTests(unittest.TestCase):
    def _report(self, **overrides):
        array, meta = load_input(seed=3, width=8, height=8)
        report = run_benchmark(
            StubEngine().predict,
            input_array=array,
            input_meta=meta,
            iterations=3,
            warmup=0,
            min_samples=2,
            environment={},
        )
        report.update(overrides)
        return report

    def test_writes_completed_report_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "report.json"
            written = write_report(self._report(), target)
            self.assertEqual(written, target)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            leftovers = [entry.name for entry in target.parent.iterdir() if entry.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_refuses_non_completed_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BenchmarkError):
                write_report(self._report(status="failed"), Path(tmp) / "report.json")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_refuses_stats_below_minimum_sample_count(self):
        report = self._report()
        report["measurements"]["stats"] = {"average_ms": 1.0}
        report["measurements"]["sample_count"] = 1  # below protocol minimum of 2
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BenchmarkError):
                write_report(report, Path(tmp) / "report.json")
            self.assertEqual(list(Path(tmp).iterdir()), [])


class ArtifactVerificationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.artifact = self.root / "model.onnx"
        self.artifact.write_bytes(b"onnx-bytes")
        self.checksum = hashlib.sha256(b"onnx-bytes").hexdigest()

    def test_matching_checksum_returns_verified_path(self):
        model = ModelVersion(name="m", framework="onnx", source_path="model.onnx", checksum=self.checksum)
        path, checksum = resolve_model_artifact(model, model_root=self.root)
        self.assertEqual(path, self.artifact)
        self.assertEqual(checksum, self.checksum)

    def test_checksum_mismatch_is_rejected(self):
        model = ModelVersion(name="m", framework="onnx", source_path="model.onnx", checksum="0" * 64)
        with self.assertRaises(BenchmarkError) as ctx:
            resolve_model_artifact(model, model_root=self.root)
        self.assertIn("does not match", str(ctx.exception))

    def test_missing_artifact_is_rejected(self):
        model = ModelVersion(name="m", framework="onnx", source_path="absent.onnx", checksum=None)
        with self.assertRaises(BenchmarkError):
            resolve_model_artifact(model, model_root=self.root)

    def test_missing_reference_is_rejected(self):
        model = ModelVersion(name="m", framework="onnx", source_path=None, checksum=None)
        with self.assertRaises(BenchmarkError) as ctx:
            resolve_model_artifact(model, model_root=self.root)
        self.assertIn("artifact reference", str(ctx.exception))

    def test_traversal_reference_is_rejected(self):
        model = ModelVersion(name="m", framework="onnx", source_path="../outside.onnx", checksum=None)
        with self.assertRaises(BenchmarkError):
            resolve_model_artifact(model, model_root=self.root)


class CliTests(unittest.TestCase):
    def test_argument_bounds_return_failure(self):
        cases = [
            ["--model-id", "1", "--iterations", "0"],
            ["--model-id", "1", "--warmup", "-1"],
            ["--model-id", "1", "--min-samples", "0"],
            ["--model-id", "1", "--width", "0"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertEqual(cli_main(argv), 2)

    def test_cli_failure_writes_no_report(self):
        """Registry unavailable => exit 2, reason printed, no report file."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            env = {**os.environ, "DATABASE_URL": f"sqlite:///{Path(tmp) / 'empty.db'}"}
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.benchmark.cli",
                    "--model-id",
                    "999",
                    "--iterations",
                    "3",
                    "--out",
                    str(out),
                ],
                cwd=BACKEND_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("error:", result.stderr)
        self.assertIn("no benchmark report was written", result.stderr)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
