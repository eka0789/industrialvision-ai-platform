"""Evaluation entry point: metrics only from a completed run on an identified split (T21).

Usage (from ``backend/``)::

    python -m app.training.evaluate \
        --run-dir data/runs/<run_id> \
        --predictions preds/val \
        --ground-truth ../datasets/neu_det/labels/val \
        --split-file val.txt \
        --iou 0.5 --conf 0.25

Contract:

- the referenced run must be **completed** — otherwise nothing is computed
  and the command exits 3;
- the split file (one relative image id per line) is hashed and recorded, so
  every reported metric names the exact split it came from;
- ground truth must exist for every listed image; missing prediction files
  are counted as empty predictions;
- the evaluation report is written next to the run and includes the run's
  dataset identity, training metrics, and the evaluation split identity.

Exit codes: 0 report written, 1 unexpected error, 3 run not completed,
4 split/ground-truth problems.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from app.training.detection_metrics import evaluate_detection, load_boxes
from app.training.runs import RunError, TrainingRun

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_COMPLETED = 3
EXIT_SPLIT_PROBLEM = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.training.evaluate",
        description="Evaluate predictions for a completed training run on an identified split.",
    )
    parser.add_argument("--run-dir", required=True, help="Completed run directory")
    parser.add_argument("--predictions", required=True, help="Directory of predicted label files")
    parser.add_argument("--ground-truth", required=True, help="Directory of ground-truth label files")
    parser.add_argument("--split-file", required=True, help="File listing image ids of the split")
    parser.add_argument("--iou", type=float, default=0.5, help="IoU match threshold")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument("--out", default=None, help="Output path (default: inside the run dir)")
    return parser


def _read_split(split_file: Path) -> tuple[list[str], str]:
    """Return (image ids, sha256 of the split file bytes)."""
    try:
        raw = split_file.read_bytes()
    except OSError as exc:
        raise ValueError(f"split file unreadable: {exc}") from exc
    image_ids = [
        line.strip()
        for line in raw.decode("utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not image_ids:
        raise ValueError("split file does not list any image ids")
    return image_ids, hashlib.sha256(raw).hexdigest()


def _label_path(base: Path, image_id: str) -> Path:
    return base / Path(image_id).with_suffix(".txt")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        run = TrainingRun.load(args.run_dir)
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if run.manifest.get("status") != "completed":
        reason = (
            f"refusing to evaluate: run {run.manifest.get('run_id')!r} status is "
            f"{run.manifest.get('status')!r}, not 'completed'"
        )
        print(f"error: {reason}", file=sys.stderr)
        return EXIT_NOT_COMPLETED

    if not 0.0 <= args.iou <= 1.0 or not 0.0 <= args.conf <= 1.0:
        print("error: --iou and --conf must lie in [0, 1]", file=sys.stderr)
        return EXIT_SPLIT_PROBLEM

    try:
        image_ids, split_sha = _read_split(Path(args.split_file))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SPLIT_PROBLEM

    predictions_dir = Path(args.predictions)
    ground_truth_dir = Path(args.ground_truth)
    ground_truth: dict[str, list] = {}
    predictions: dict[str, list] = {}
    missing_predictions = 0

    for image_id in image_ids:
        gt_path = _label_path(ground_truth_dir, image_id)
        try:
            ground_truth[image_id] = load_boxes(gt_path)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SPLIT_PROBLEM
        pred_path = _label_path(predictions_dir, image_id)
        if pred_path.is_file():
            try:
                predictions[image_id] = load_boxes(pred_path)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_SPLIT_PROBLEM
        else:
            predictions[image_id] = []
            missing_predictions += 1

    try:
        metrics = evaluate_detection(
            ground_truth,
            predictions,
            iou_threshold=args.iou,
            confidence_threshold=args.conf,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SPLIT_PROBLEM

    try:
        training = run.publish_metrics()
    except RunError as exc:  # defensive: status was already checked
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_COMPLETED

    report = {
        "schema_version": "1.0",
        "tool": "industrialvision-evaluate",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run_id": run.manifest["run_id"],
        "task": run.manifest["task"],
        "seed": run.manifest["seed"],
        "run_dataset": run.manifest["dataset"],
        "evaluation_split": {
            "file": Path(args.split_file).name,
            "sha256": split_sha,
            "items": len(image_ids),
        },
        "parameters": {
            "iou_threshold": args.iou,
            "confidence_threshold": args.conf,
        },
        "checkpoints": run.manifest["checkpoints"],
        "training_metrics": training["metrics"],
        "missing_prediction_files": missing_predictions,
        "metrics": metrics,
        "status": "completed",
    }

    out_path = Path(args.out) if args.out else run.run_dir / f"evaluation_{split_sha[:12]}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    print(f"run {run.manifest['run_id']} | split {Path(args.split_file).name} ({len(image_ids)} images)")
    print(
        f"precision={metrics['precision']} recall={metrics['recall']} "
        f"f1={metrics['f1']} map={metrics['map']}"
    )
    if missing_predictions:
        print(f"note: {missing_predictions} image(s) had no prediction file (treated as empty)")
    print(f"report -> {out_path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
