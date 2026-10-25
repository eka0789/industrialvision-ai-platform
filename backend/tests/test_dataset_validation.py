"""Dataset validation, deterministic split, and leakage detection tests (T18)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zlib
from pathlib import Path

import cv2
import numpy as np

from app.datasets.cli import main as cli_main
from app.datasets.split import assign_groups, group_by_source_identity, source_stem
from app.datasets.validation import build_report, report_bytes


def png_bytes(seed: int | None = None) -> bytes:
    if seed is None:
        image = np.zeros((8, 8, 3), dtype=np.uint8)
    else:
        rng = np.random.default_rng(seed)
        image = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return encoded.tobytes()


def jpeg_bytes(seed: int | None = None) -> bytes:
    if seed is None:
        image = np.zeros((8, 8, 3), dtype=np.uint8)
    else:
        rng = np.random.default_rng(seed)
        image = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return encoded.tobytes()


def write_image(root: Path, rel: str, content: bytes | None = None) -> None:
    """Write an image; default content is unique per relative path."""
    path = root / "images" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = png_bytes(seed=zlib.crc32(rel.encode("utf-8")))
    path.write_bytes(content)


def write_label(root: Path, rel: str, content: str) -> None:
    path = root / "labels" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def clean_dataset(root: Path, count: int = 6) -> None:
    for index in range(count):
        write_image(root, f"item_{index:03d}.png")
        write_label(root, f"item_{index:03d}.txt", f"0 {0.25 + index * 0.05:.2f} 0.5 0.2 0.3\n")


class DatasetValidationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "dataset"

    def test_clean_dataset_reports_ok_with_counts(self):
        clean_dataset(self.root, count=6)
        report = build_report(self.root, num_classes=3, seed=7)

        self.assertTrue(report["summary"]["ok"], report["issues"])
        self.assertEqual(report["summary"]["images"], 6)
        self.assertEqual(report["summary"]["paired_labels"], 6)
        self.assertEqual(report["summary"]["boxes"], 6)
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertEqual(report["summary"]["class_histogram"], {"0": 6})
        self.assertEqual(report["layout"], "flat")
        self.assertEqual(report["schema_version"], "1.0")

    def test_split_is_deterministic_and_partitions_all_files(self):
        clean_dataset(self.root, count=8)
        first = build_report(self.root, seed=123, ratios=(0.5, 0.5), split_names=("train", "test"))
        second = build_report(self.root, seed=123, ratios=(0.5, 0.5), split_names=("train", "test"))

        self.assertEqual(first["split"]["files"], second["split"]["files"])
        train = set(first["split"]["files"]["train"])
        test = set(first["split"]["files"]["test"])
        self.assertEqual(train | test, {f"item_{index:03d}.png" for index in range(8)})
        self.assertFalse(train & test)
        self.assertEqual(first["fingerprint"]["value"], second["fingerprint"]["value"])

    def test_split_grouping_keeps_source_identity_together(self):
        # "frame_0001"/"frame_0002" share a source stem, so they must land in one split.
        for index in (1, 2):
            write_image(self.root, f"frame_{index:04d}.png")
            write_label(self.root, f"frame_{index:04d}.txt", "0 0.5 0.5 0.4 0.4\n")
        for name in ("other_a", "other_b", "other_c", "other_d"):
            write_image(self.root, f"{name}.png")
            write_label(self.root, f"{name}.txt", "1 0.5 0.5 0.4 0.4\n")

        report = build_report(self.root, seed=99, ratios=(0.5, 0.5), split_names=("train", "test"))
        assignment: dict[str, str] = {}
        for split, files in report["split"]["files"].items():
            for name in files:
                assignment[name] = split
        self.assertEqual(assignment["frame_0001.png"], assignment["frame_0002.png"])

    def test_pairing_class_box_and_file_issues_are_reported(self):
        clean_dataset(self.root, count=1)
        write_image(self.root, "no_label.png")
        write_label(self.root, "orphan.txt", "0 0.5 0.5 0.1 0.1\n")
        write_label(self.root, "item_000.txt", "7 0.5 0.5 0.1 0.1\n")  # class out of range
        write_image(self.root, "empty.png", content=b"")
        write_image(self.root, "corrupt.png", content=b"definitely not an image")
        write_image(self.root, "bad_box.png")
        write_label(self.root, "bad_box.txt", "0 0.95 0.95 0.5 0.5\n")  # extends outside
        write_image(self.root, "zero_box.png")
        write_label(self.root, "zero_box.txt", "0 0.5 0.5 0 0.2\n")
        write_image(self.root, "neg.png")
        write_label(self.root, "neg.txt", "  \n")  # empty label = valid negative sample
        write_image(self.root, "dup.png")
        write_image(self.root, "dup.jpg", content=jpeg_bytes(seed=7))
        write_label(self.root, "dup.txt", "0 0.5 0.5 0.2 0.2\n")

        report = build_report(self.root, num_classes=3)
        codes = {issue["code"] for issue in report["issues"]}

        self.assertFalse(report["summary"]["ok"])
        self.assertIn("missing_label", codes)
        self.assertIn("orphan_label", codes)
        self.assertIn("class_id_out_of_range", codes)
        self.assertIn("empty_image", codes)
        self.assertIn("corrupt_image", codes)
        self.assertIn("bbox_out_of_bounds", codes)
        self.assertIn("bbox_invalid_size", codes)
        self.assertIn("duplicate_image_stem", codes)
        self.assertEqual(report["summary"]["empty_labels"], 1)

    def test_mask_polygon_labels_are_validated(self):
        write_image(self.root, "mask.png")
        write_label(self.root, "mask.txt", "1 0.1 0.1 0.5 0.1 0.5 0.5 0.1 0.5\n")
        report = build_report(self.root, num_classes=2)
        self.assertEqual(report["summary"]["polygons"], 1)
        self.assertTrue(report["summary"]["ok"], report["issues"])

        write_label(self.root, "mask.txt", "1 0.2 0.2 0.6 0.2 0.6 0.2 0.2 0.2\n")
        report = build_report(self.root, num_classes=2)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("polygon_degenerate", codes)

    def test_split_layout_detects_identity_leakage(self):
        write_image(self.root, "train/frame_1.png")
        write_label(self.root, "train/frame_1.txt", "0 0.5 0.5 0.2 0.2\n")
        write_image(self.root, "val/frame_2.png")
        write_label(self.root, "val/frame_2.txt", "0 0.5 0.5 0.2 0.2\n")

        report = build_report(self.root, seed=1)
        self.assertEqual(report["layout"], "split")
        self.assertFalse(report["leakage"]["ok"])
        self.assertEqual(len(report["leakage"]["conflicts"]), 1)
        self.assertEqual(report["leakage"]["conflicts"][0]["splits"], ["train", "val"])
        self.assertFalse(report["summary"]["ok"])
        self.assertEqual(report["split"]["performed"], False)
        self.assertEqual(report["split"]["source"], "existing_directories")

    def test_checksums_and_fingerprint_match_file_bytes(self):
        content = png_bytes(seed=1)
        write_image(self.root, "only.png", content=content)
        write_label(self.root, "only.txt", "0 0.5 0.5 0.1 0.1\n")
        report = build_report(self.root, with_checksums=True)

        expected = hashlib.sha256(content).hexdigest()
        self.assertEqual(report["checksums"]["files"]["images/only.png"], expected)
        label_digest = hashlib.sha256(b"0 0.5 0.5 0.1 0.1\n").hexdigest()
        self.assertEqual(report["checksums"]["files"]["labels/only.txt"], label_digest)
        self.assertEqual(len(report["fingerprint"]["value"]), 64)

    def test_report_bytes_are_json_serializable_and_stable(self):
        clean_dataset(self.root, count=3)
        report = build_report(self.root, seed=5)
        payload = report_bytes(report)
        reloaded = json.loads(payload.decode("utf-8"))
        self.assertEqual(reloaded["summary"]["images"], 3)
        self.assertEqual(payload, report_bytes(json.loads(payload.decode("utf-8"))))

    def test_missing_root_raises(self):
        with self.assertRaises(ValueError):
            build_report(Path(self._temp.name) / "does-not-exist")

    def test_missing_directories_produce_machine_readable_errors(self):
        self.root.mkdir(parents=True, exist_ok=True)
        report = build_report(self.root)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("missing_images_dir", codes)
        self.assertFalse(report["summary"]["ok"])


class SplitHelperTests(unittest.TestCase):
    def test_source_stem_strips_trailing_index(self):
        self.assertEqual(source_stem("img_0001.png"), "img")
        self.assertEqual(source_stem("batch-12.tif"), "batch")
        self.assertEqual(source_stem("plain.png"), "plain")

    def test_assign_groups_is_order_independent(self):
        groups = [["a_1.png", "a_2.png"], ["b.png"], ["c.png"], ["d.png"]]
        forward = assign_groups(groups, seed=3, ratios=(0.5, 0.5), split_names=("train", "test"))
        backward = assign_groups(list(reversed(groups)), seed=3, ratios=(0.5, 0.5), split_names=("train", "test"))
        self.assertEqual(forward, backward)

    def test_assign_groups_rejects_invalid_ratios(self):
        with self.assertRaises(ValueError):
            assign_groups([["a.png"]], seed=1, ratios=(0.9, 0.9))
        with self.assertRaises(ValueError):
            assign_groups([["a.png"]], seed=1, ratios=(0.5,), split_names=("train",))

    def test_group_by_content_hash_and_stem(self):
        files = ["x.png", "y.png", "z.png"]
        hashes = {"x.png": "aaa", "y.png": "aaa", "z.png": "bbb"}
        groups = group_by_source_identity(files, hashes)
        sizes = sorted(len(group) for group in groups)
        self.assertEqual(sizes, [1, 2])


class DatasetCliTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "dataset"
        self.report_path = Path(self._temp.name) / "out" / "report.json"

    def test_cli_writes_report_and_exit_codes(self):
        clean_dataset(self.root, count=4)
        code = cli_main([str(self.root), "--report", str(self.report_path)])
        self.assertEqual(code, 0)
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["summary"]["ok"])

        # corrupt the dataset -> exit code 1
        write_image(self.root, "broken.png", content=b"garbage")
        code = cli_main([str(self.root), "--report", str(self.report_path)])
        self.assertEqual(code, 1)

    def test_cli_missing_root_exits_2(self):
        code = cli_main([str(Path(self._temp.name) / "nope"), "--report", str(self.report_path)])
        self.assertEqual(code, 2)
        self.assertFalse(self.report_path.exists())


if __name__ == "__main__":
    unittest.main()
