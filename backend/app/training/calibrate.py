"""Confidence threshold calibration sweep for factory QA validation.

Sweeps a list of confidence thresholds on validation predictions vs ground truth
so QA/AI engineers can select an operating threshold that balances Escape Rate
(false negatives) and False Reject Rate (false positives) before production cutover.

Usage (from ``backend/``)::

    python -m app.training.calibrate \
        --predictions preds/val \
        --ground-truth ../datasets/plant_val/labels \
        --split-file val.txt \
        --iou 0.5 \
        --thresholds 0.15,0.25,0.35,0.45,0.55,0.65,0.75
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.training.detection_metrics import evaluate_detection, load_boxes
from app.training.evaluate import EXIT_SPLIT_PROBLEM, _label_path, _read_split


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.training.calibrate",
        description="Sweep confidence thresholds to calibrate factory escape vs false-reject rates.",
    )
    parser.add_argument("--predictions", required=True, help="Directory of predicted label files")
    parser.add_argument("--ground-truth", required=True, help="Directory of ground-truth label files")
    parser.add_argument("--split-file", required=True, help="File listing image ids of the validation split")
    parser.add_argument("--iou", type=float, default=0.5, help="IoU match threshold")
    parser.add_argument(
        "--thresholds",
        default="0.15,0.25,0.35,0.45,0.55,0.65,0.75,0.85",
        help="Comma-separated confidence thresholds to evaluate",
    )
    parser.add_argument("--out", default=None, help="Optional JSON output file path")
    return parser


def sweep_thresholds(
    *,
    ground_truth: dict[str, list],
    predictions: dict[str, list],
    thresholds: list[float],
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for conf in thresholds:
        metrics = evaluate_detection(
            ground_truth=ground_truth,
            predictions=predictions,
            iou_threshold=iou_threshold,
            confidence_threshold=conf,
        )
        tp = int(metrics["tp"])
        fp = int(metrics["fp"])
        fn = int(metrics["fn"])
        total_gt = tp + fn
        total_pred = tp + fp
        escape_rate = round(fn / total_gt, 4) if total_gt > 0 else 0.0
        false_reject_rate = round(fp / total_pred, 4) if total_pred > 0 else 0.0
        results.append(
            {
                "conf_threshold": conf,
                "precision": round(float(metrics["precision"]), 4),
                "recall": round(float(metrics["recall"]), 4),
                "f1": round(float(metrics["f1"]), 4),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "escape_rate": escape_rate,
                "false_reject_rate": false_reject_rate,
            }
        )

    best = max(results, key=lambda r: (r["f1"], -r["escape_rate"])) if results else None
    return {
        "iou_threshold": iou_threshold,
        "recommended_by_f1": best["conf_threshold"] if best else None,
        "sweep": results,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0.0 <= args.iou <= 1.0:
        print("error: --iou must lie in [0, 1]", file=sys.stderr)
        return EXIT_SPLIT_PROBLEM

    try:
        thresholds = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
        if not thresholds or any(not (0.0 <= t <= 1.0) for t in thresholds):
            raise ValueError("all thresholds must lie in [0, 1]")
    except ValueError as exc:
        print(f"error: invalid --thresholds: {exc}", file=sys.stderr)
        return EXIT_SPLIT_PROBLEM

    try:
        image_ids, split_sha = _read_split(Path(args.split_file))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SPLIT_PROBLEM

    pred_dir = Path(args.predictions)
    gt_dir = Path(args.ground_truth)
    ground_truth: dict[str, list] = {}
    predictions: dict[str, list] = {}

    for image_id in image_ids:
        try:
            ground_truth[image_id] = load_boxes(_label_path(gt_dir, image_id))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SPLIT_PROBLEM
        pred_path = _label_path(pred_dir, image_id)
        try:
            predictions[image_id] = load_boxes(pred_path, require_confidence=True) if pred_path.exists() else []
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SPLIT_PROBLEM

    report = sweep_thresholds(
        ground_truth=ground_truth,
        predictions=predictions,
        thresholds=thresholds,
        iou_threshold=args.iou,
    )
    report["split_sha256"] = split_sha
    report["num_images"] = len(image_ids)

    payload = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
