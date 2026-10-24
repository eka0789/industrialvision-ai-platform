"""Training-run lifecycle, detection metrics, and CLI gate tests (T21)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.training.detection_metrics import Box, evaluate_detection, iou, load_boxes
from app.training.runs import RunError, TrainingRun
from tests.test_dataset_validation import write_image, write_label

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def run_module(module: str, argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *argv],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def make_run(base_dir: Path, *, status: str = "created", dataset: dict | None = None) -> TrainingRun:
    run = TrainingRun.create(
        base_dir,
        task="object_detection",
        seed=42,
        config={"model": "yolo11n.pt", "data": "fixture.yaml", "epochs": 1},
        dataset=dataset
        or {
            "fingerprint": "a" * 64,
            "split": "val",
            "split_items": 2,
            "split_sha256": "b" * 64,
            "report_sha256": "c" * 64,
        },
    )
    if status in ("running", "completed", "failed"):
        run.start()
    if status == "completed":
        (run.run_dir / "fit").mkdir(parents=True, exist_ok=True)
        (run.run_dir / "fit" / "best.pt").write_bytes(b"weights")
        run.complete(checkpoints=["fit/best.pt"], metrics={"mAP50": 0.5, "precision": 0.75})
    elif status == "failed":
        run.fail("fixture failure")
    return run


class TrainingRunTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name) / "runs"

    def test_create_requires_dataset_identity(self):
        with self.assertRaises(RunError):
            TrainingRun.create(self.base, task="object_detection", seed=1, config={}, dataset={})
        with self.assertRaises(RunError):
            TrainingRun.create(
                self.base, task="object_detection", seed=1, config={}, dataset={"fingerprint": "a" * 64}
            )
        with self.assertRaises(RunError):
            TrainingRun.create(
                self.base, task="object_detection", seed=1, config={}, dataset={"split": "val"}
            )

    def test_create_validates_seed_config_and_run_id(self):
        with self.assertRaises(RunError):
            TrainingRun.create(
                self.base, task="object_detection", seed="42", config={}, dataset={"fingerprint": "a", "split": "val"}
            )
        with self.assertRaises(RunError):
            TrainingRun.create(
                self.base,
                task="object_detection",
                seed=1,
                config={"bad": object()},
                dataset={"fingerprint": "a", "split": "val"},
            )
        with self.assertRaises(RunError):
            TrainingRun.create(
                self.base,
                task="object_detection",
                seed=1,
                config={},
                dataset={"fingerprint": "a", "split": "val"},
                run_id="../escape",
            )

    def test_duplicate_run_id_is_rejected(self):
        identity = {"fingerprint": "a" * 64, "split": "val"}
        TrainingRun.create(
            self.base, task="object_detection", seed=1, config={}, dataset=identity, run_id="same-run"
        )
        with self.assertRaises(RunError):
            TrainingRun.create(
                self.base, task="object_detection", seed=1, config={}, dataset=identity, run_id="same-run"
            )

    def test_metrics_gated_until_completed_run(self):
        created = make_run(self.base, status="created")
        with self.assertRaises(RunError):
            created.publish_metrics()
        self.assertFalse((created.run_dir / "metrics.json").exists())

        started = make_run(self.base, status="running")
        with self.assertRaises(RunError):
            started.publish_metrics()  # running, not completed
        # cannot complete without having started through the proper path
        failed = make_run(self.base, status="failed")
        with self.assertRaises(RunError):
            failed.publish_metrics()

        completed = make_run(self.base, status="completed")
        published = completed.publish_metrics()
        self.assertEqual(published["dataset"]["split"], "val")
        self.assertEqual(published["metrics"]["mAP50"], 0.5)
        self.assertTrue((completed.run_dir / "metrics.json").is_file())

    def test_invalid_transitions_and_completion_requirements(self):
        run = make_run(self.base, status="created")
        with self.assertRaises(RunError):  # cannot complete before start
            run.complete(checkpoints=["fit/best.pt"], metrics={"x": 1.0})
        run.start()
        run.fail("boom")
        with self.assertRaises(RunError):  # failed runs cannot be resurrected
            run.start()
        with self.assertRaises(RunError):
            run.complete(checkpoints=["fit/best.pt"], metrics={"x": 1.0})

        fresh = make_run(self.base, status="running")
        with self.assertRaises(RunError):  # empty metrics refused
            fresh.complete(checkpoints=["fit/best.pt"], metrics={})
        with self.assertRaises(RunError):  # missing checkpoint file refused
            fresh.complete(checkpoints=["fit/ghost.pt"], metrics={"x": 1.0})
        (fresh.run_dir / "fit").mkdir(parents=True, exist_ok=True)
        (fresh.run_dir / "fit" / "best.pt").write_bytes(b"w")
        with self.assertRaises(RunError):  # absolute path refused
            fresh.complete(checkpoints=["/etc/passwd"], metrics={"x": 1.0})
        with self.assertRaises(RunError):  # traversal refused
            fresh.complete(checkpoints=["../outside.pt"], metrics={"x": 1.0})

    def test_manifest_roundtrip_and_log(self):
        run = make_run(self.base, status="completed")
        loaded = TrainingRun.load(run.run_dir)
        self.assertEqual(loaded.manifest["status"], "completed")
        self.assertEqual(loaded.manifest["seed"], 42)
        self.assertEqual(loaded.manifest["checkpoints"], ["fit/best.pt"])
        self.assertIn("python", loaded.manifest["environment"])
        log_text = (run.run_dir / "run.log").read_text(encoding="utf-8")
        self.assertIn("run started", log_text)
        self.assertIn("run completed", log_text)
        manifest_on_disk = json.loads((run.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_on_disk["status"], "completed")
        with self.assertRaises(RunError):
            TrainingRun.load(Path(self._temp.name) / "missing")


class DetectionMetricsTests(unittest.TestCase):
    @staticmethod
    def gt_box(class_id: int = 0) -> Box:
        return Box(class_id=class_id, x1=0.1, y1=0.1, x2=0.5, y2=0.5)

    def test_iou_known_values(self):
        self.assertAlmostEqual(iou(Box(0, 0.0, 0.0, 1.0, 1.0), Box(0, 0.5, 0.0, 1.5, 1.0)), 1 / 3, places=6)
        self.assertEqual(iou(Box(0, 0.0, 0.0, 0.2, 0.2), Box(0, 0.5, 0.5, 0.7, 0.7)), 0.0)

    def test_perfect_predictions(self):
        metrics = evaluate_detection(
            {"img": [self.gt_box()]},
            {"img": [Box(0, 0.1, 0.1, 0.5, 0.5, conf=0.9)]},
        )
        self.assertEqual(metrics["precision"], 1.0)
        self.assertEqual(metrics["recall"], 1.0)
        self.assertEqual(metrics["f1"], 1.0)
        self.assertEqual(metrics["map"], 1.0)
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fp"], 0)
        self.assertEqual(metrics["fn"], 0)

    def test_false_positive_lowers_precision_only(self):
        metrics = evaluate_detection(
            {"img": [self.gt_box()]},
            {"img": [Box(0, 0.1, 0.1, 0.5, 0.5, conf=0.9), Box(0, 0.6, 0.6, 0.9, 0.9, conf=0.8)]},
        )
        self.assertAlmostEqual(metrics["precision"], 0.5, places=6)
        self.assertAlmostEqual(metrics["recall"], 1.0, places=6)
        self.assertAlmostEqual(metrics["f1"], 2 / 3, places=6)

    def test_confidence_threshold_filters_predictions(self):
        metrics = evaluate_detection(
            {"i1": [self.gt_box()], "i2": [self.gt_box()]},
            {"i1": [Box(0, 0.1, 0.1, 0.5, 0.5, conf=0.3)]},
            confidence_threshold=0.5,
        )
        self.assertEqual(metrics["predictions"], 0)
        self.assertEqual(metrics["fn"], 2)
        self.assertEqual(metrics["precision"], 0.0)
        self.assertIsNone(metrics["map"])  # nothing to rank -> no AP claimed

    def test_wrong_class_counts_as_false_positive_and_false_negative(self):
        metrics = evaluate_detection(
            {"img": [self.gt_box(0)]},
            {"img": [Box(1, 0.1, 0.1, 0.5, 0.5, conf=0.9)]},
        )
        self.assertEqual(metrics["tp"], 0)
        self.assertEqual(metrics["fp"], 1)
        self.assertEqual(metrics["fn"], 1)
        self.assertEqual(metrics["per_class"]["0"]["fn"], 1)
        self.assertEqual(metrics["per_class"]["1"]["fp"], 1)

    def test_average_precision_interpolation(self):
        gt = {
            "i1": [Box(0, 0.0, 0.0, 0.4, 0.4)],
            "i2": [Box(0, 0.6, 0.6, 1.0, 1.0)],
        }
        preds = {
            "i1": [Box(0, 0.0, 0.0, 0.4, 0.4, conf=0.9)],
            "i2": [
                Box(0, 0.3, 0.3, 0.6, 0.6, conf=0.8),  # wrong location
                Box(0, 0.6, 0.6, 1.0, 1.0, conf=0.7),  # correct
            ],
        }
        metrics = evaluate_detection(gt, preds)
        self.assertEqual(metrics["tp"], 2)
        self.assertEqual(metrics["fp"], 1)
        self.assertAlmostEqual(metrics["map"], 0.833333, places=5)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            evaluate_detection({}, {}, )
        with self.assertRaises(ValueError):
            evaluate_detection({"i": [self.gt_box()]}, {}, iou_threshold=1.5)

    def test_load_boxes_from_label_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.txt"
            good.write_text("0 0.5 0.5 0.2 0.2 0.9\n", encoding="utf-8")
            boxes = load_boxes(good)
            self.assertEqual(len(boxes), 1)
            self.assertAlmostEqual(boxes[0].conf, 0.9)
            self.assertAlmostEqual(boxes[0].x1, 0.4)

            bad = root / "bad.txt"
            bad.write_text("0 0.5\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_boxes(bad)
            with self.assertRaises(ValueError):
                load_boxes(root / "missing.txt")


class TrainingCliTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.dataset = self.root / "dataset"
        self.runs = self.root / "runs"
        self._build_dataset()
        self.report_path = self.root / "report.json"
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "seed": 7,
                    "task": "object_detection",
                    "model": "yolo11n.pt",
                    "data": "fixture.yaml",
                    "epochs": 1,
                }
            ),
            encoding="utf-8",
        )
        self._write_report()

    def _build_dataset(self) -> None:
        # Distinct stems -> each file is its own source group, so the "train"
        # split stays populated for this seed/ratio combination.
        for stem in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
            write_image(self.dataset, f"{stem}.png")
            write_label(self.dataset, f"{stem}.txt", "0 0.5 0.5 0.3 0.3\n")

    def _write_report(self, *, corrupt: bool = False) -> None:
        if corrupt:
            (self.dataset / "images" / "broken.png").write_bytes(b"garbage")
        from app.datasets.validation import build_report

        report = build_report(self.dataset, num_classes=3)
        self.assertTrue(report["split"]["counts"].get("train"), "fixture needs a non-empty train split")
        self.report_path.write_text(json.dumps(report), encoding="utf-8")

    def test_missing_training_runtime_fails_run_without_metrics(self):
        try:
            import ultralytics  # noqa: F401
        except Exception:
            pass
        else:
            self.skipTest("ultralytics is installed; missing-runtime path not reproducible")

        result = run_module(
            "app.training.train",
            [
                "--dataset-root", str(self.dataset),
                "--report", str(self.report_path),
                "--config", str(self.config_path),
                "--runs-dir", str(self.runs),
            ],
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        run_dirs = [path for path in self.runs.iterdir() if path.is_dir()]
        self.assertEqual(len(run_dirs), 1)
        manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("ultralytics", manifest["reason"])
        self.assertEqual(manifest["seed"], 7)
        self.assertEqual(manifest["dataset"]["split"], "train")
        self.assertFalse((run_dirs[0] / "metrics.json").exists())
        self.assertTrue((run_dirs[0] / "run.log").is_file())

    def test_failing_dataset_report_is_refused_before_any_run(self):
        self._write_report(corrupt=True)
        result = run_module(
            "app.training.train",
            [
                "--dataset-root", str(self.dataset),
                "--report", str(self.report_path),
                "--config", str(self.config_path),
                "--runs-dir", str(self.runs),
            ],
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("validation", result.stderr)
        self.assertFalse(self.runs.exists())

    def test_report_from_different_root_is_refused(self):
        other = self.root / "other_dataset"
        for stem in ("one", "two", "three", "four"):
            write_image(other, f"{stem}.png")
            write_label(other, f"{stem}.txt", "0 0.5 0.5 0.3 0.3\n")
        from app.datasets.validation import build_report

        foreign = build_report(other, num_classes=3)
        self.report_path.write_text(json.dumps(foreign), encoding="utf-8")
        result = run_module(
            "app.training.train",
            [
                "--dataset-root", str(self.dataset),
                "--report", str(self.report_path),
                "--config", str(self.config_path),
                "--runs-dir", str(self.runs),
            ],
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("mismatch", result.stderr)
        self.assertFalse(self.runs.exists())

    def test_missing_seed_is_refused(self):
        self.config_path.write_text(
            json.dumps({"model": "yolo11n.pt", "data": "fixture.yaml"}), encoding="utf-8"
        )
        result = run_module(
            "app.training.train",
            [
                "--dataset-root", str(self.dataset),
                "--report", str(self.report_path),
                "--config", str(self.config_path),
                "--runs-dir", str(self.runs),
            ],
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("seed", result.stderr)


class EvaluateCliTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.runs = self.root / "runs"
        self.gt = self.root / "labels_gt"
        self.preds = self.root / "labels_pred"
        self.gt.mkdir()
        self.preds.mkdir()
        (self.gt / "a.txt").write_text("0 0.5 0.5 0.4 0.4\n", encoding="utf-8")
        (self.gt / "b.txt").write_text("0 0.5 0.5 0.4 0.4\n", encoding="utf-8")
        (self.preds / "a.txt").write_text("0 0.5 0.5 0.4 0.4 0.9\n", encoding="utf-8")
        self.split_file = self.root / "val.txt"
        self.split_file.write_text("a.png\nb.png\n", encoding="utf-8")

    def _argv(self, run_dir: Path) -> list[str]:
        return [
            "--run-dir", str(run_dir),
            "--predictions", str(self.preds),
            "--ground-truth", str(self.gt),
            "--split-file", str(self.split_file),
        ]

    def test_refuses_run_that_is_not_completed(self):
        run = make_run(self.runs, status="failed")
        result = run_module("app.training.evaluate", self._argv(run.run_dir))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("completed", result.stderr)
        self.assertEqual(list(run.run_dir.glob("evaluation_*.json")), [])

    def test_completes_with_metrics_bound_to_identified_split(self):
        run = make_run(self.runs, status="completed")
        result = run_module("app.training.evaluate", self._argv(run.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        reports = list(run.run_dir.glob("evaluation_*.json"))
        self.assertEqual(len(reports), 1)
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["run_id"], run.manifest["run_id"])
        self.assertEqual(payload["evaluation_split"]["items"], 2)
        self.assertEqual(len(payload["evaluation_split"]["sha256"]), 64)
        self.assertEqual(payload["run_dataset"]["fingerprint"], "a" * 64)
        self.assertEqual(payload["training_metrics"]["mAP50"], 0.5)
        self.assertEqual(payload["missing_prediction_files"], 1)
        self.assertEqual(payload["metrics"]["tp"], 1)
        self.assertEqual(payload["metrics"]["fn"], 1)
        self.assertAlmostEqual(payload["metrics"]["recall"], 0.5, places=6)
        self.assertAlmostEqual(payload["metrics"]["precision"], 1.0, places=6)

    def test_missing_ground_truth_exits_4(self):
        run = make_run(self.runs, status="completed")
        (self.split_file).write_text("a.png\nghost.png\n", encoding="utf-8")
        result = run_module("app.training.evaluate", self._argv(run.run_dir))
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual(list(run.run_dir.glob("evaluation_*.json")), [])

    def test_empty_split_exits_4(self):
        run = make_run(self.runs, status="completed")
        self.split_file.write_text("\n  \n", encoding="utf-8")
        result = run_module("app.training.evaluate", self._argv(run.run_dir))
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
