"""Detection metrics for YOLO-style boxes (T21), pure Python.

Computes IoU-matched precision/recall/F1 and per-class average precision
(VOC-style all-point interpolation) over normalized ``xyxy`` boxes. Ground
truth and predictions are keyed by image identifier (relative path), which
ties every metric to an identified split.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from app.datasets.labels import LabelRecord, parse_label_file

_EPS = 1e-9


@dataclass(frozen=True)
class Box:
    """Normalized xyxy box."""

    class_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 1.0

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


def iou(left: Box, right: Box) -> float:
    """Intersection-over-union of two boxes (normalized coordinates)."""
    inter_x1 = max(left.x1, right.x1)
    inter_y1 = max(left.y1, right.y1)
    inter_x2 = min(left.x2, right.x2)
    inter_y2 = min(left.y2, right.y2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    intersection = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    union = left.area + right.area - intersection
    if union <= _EPS:
        return 0.0
    return intersection / union


def boxes_from_records(records: Sequence[LabelRecord]) -> list[Box]:
    """Convert parsed YOLO detection records to xyxy boxes (masks are skipped)."""
    boxes: list[Box] = []
    for record in records:
        if record.kind != "box":
            continue
        cx, cy, width, height = record.coords
        conf = 1.0 if record.conf is None else record.conf
        boxes.append(
            Box(
                class_id=record.class_id,
                x1=cx - width / 2,
                y1=cy - height / 2,
                x2=cx + width / 2,
                y2=cy + height / 2,
                conf=conf,
            )
        )
    return boxes


def load_boxes(label_path: Path, *, num_classes: int | None = None) -> list[Box]:
    """Load boxes from a YOLO label file; invalid lines raise ValueError."""
    if not label_path.is_file():
        raise ValueError(f"label file not found: {label_path}")
    records, issues = parse_label_file(label_path, num_classes=num_classes)
    if issues:
        first = issues[0]
        location = f"{label_path}:{first.line}" if first.line else str(label_path)
        raise ValueError(f"invalid label file ({location}): {first.code} {first.message}")
    return boxes_from_records(records)


def evaluate_detection(
    ground_truth: Mapping[str, Sequence[Box]],
    predictions: Mapping[str, Sequence[Box]],
    *,
    iou_threshold: float = 0.5,
    confidence_threshold: float = 0.0,
    class_ids: Iterable[int] | None = None,
) -> dict:
    """Evaluate predicted boxes against ground truth on an identified image set."""
    if not 0.0 <= iou_threshold <= 1.0:
        raise ValueError("iou_threshold must lie in [0, 1]")
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must lie in [0, 1]")

    image_ids = sorted(set(ground_truth) | set(predictions))
    if not image_ids:
        raise ValueError("evaluation requires at least one image identifier")

    filtered_predictions = {
        image_id: [box for box in predictions.get(image_id, ()) if box.conf >= confidence_threshold]
        for image_id in image_ids
    }

    counts: dict[int, dict[str, int]] = {}

    def bucket(class_id: int) -> dict[str, int]:
        return counts.setdefault(
            class_id, {"support": 0, "tp": 0, "fp": 0, "fn": 0}
        )

    total_tp = total_fp = total_fn = 0
    detection_records: dict[int, list[tuple[float, int]]] = {}

    for image_id in image_ids:
        gt_boxes = list(ground_truth.get(image_id, ()))
        pred_boxes = sorted(filtered_predictions[image_id], key=lambda box: box.conf, reverse=True)
        matched: set[int] = set()

        for box in gt_boxes:
            bucket(box.class_id)["support"] += 1

        for pred in pred_boxes:
            best_index = -1
            best_iou = iou_threshold
            for index, gt in enumerate(gt_boxes):
                if index in matched or gt.class_id != pred.class_id:
                    continue
                overlap = iou(pred, gt)
                if overlap >= best_iou:
                    best_iou = overlap
                    best_index = index
            if best_index >= 0:
                matched.add(best_index)
                bucket(pred.class_id)["tp"] += 1
                detection_records.setdefault(pred.class_id, []).append((pred.conf, 1))
                total_tp += 1
            else:
                bucket(pred.class_id)["fp"] += 1
                detection_records.setdefault(pred.class_id, []).append((pred.conf, 0))
                total_fp += 1

        for index, gt in enumerate(gt_boxes):
            if index not in matched:
                bucket(gt.class_id)["fn"] += 1
                total_fn += 1

    selected_classes = sorted(class_ids) if class_ids is not None else sorted(counts)
    precision = _divide(total_tp, total_tp + total_fp)
    recall = _divide(total_tp, total_tp + total_fn)
    f1 = _divide(2 * precision * recall, precision + recall)

    per_class: dict[str, dict] = {}
    ap_values: list[float] = []
    for class_id in selected_classes:
        stats = counts.get(class_id, {"support": 0, "tp": 0, "fp": 0, "fn": 0})
        class_precision = _divide(stats["tp"], stats["tp"] + stats["fp"])
        class_recall = _divide(stats["tp"], stats["tp"] + stats["fn"])
        class_f1 = _divide(2 * class_precision * class_recall, class_precision + class_recall)
        ap = _average_precision(detection_records.get(class_id, []), stats["support"])
        if ap is not None:
            ap_values.append(ap)
        per_class[str(class_id)] = {
            "support": stats["support"],
            "tp": stats["tp"],
            "fp": stats["fp"],
            "fn": stats["fn"],
            "precision": round(class_precision, 6),
            "recall": round(class_recall, 6),
            "f1": round(class_f1, 6),
            "ap": None if ap is None else round(ap, 6),
        }

    return {
        "images": len(image_ids),
        "ground_truth": sum(stats["support"] for stats in counts.values()),
        "predictions": sum(len(boxes) for boxes in filtered_predictions.values()),
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "iou_threshold": iou_threshold,
        "confidence_threshold": confidence_threshold,
        "per_class": per_class,
        "map": round(sum(ap_values) / len(ap_values), 6) if ap_values else None,
    }


def _divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > _EPS else 0.0


def _average_precision(records: list[tuple[float, int]], support: int) -> float | None:
    """VOC-style all-point interpolated AP from (confidence, is_tp) records."""
    if support <= 0 or not records:
        return None
    ordered = sorted(records, key=lambda item: item[0], reverse=True)
    tp_cumulative = 0
    fp_cumulative = 0
    points: list[tuple[float, float]] = []  # (recall, precision)
    for _conf, is_tp in ordered:
        tp_cumulative += 1 if is_tp else 0
        fp_cumulative += 0 if is_tp else 1
        recall = tp_cumulative / support
        precision = tp_cumulative / (tp_cumulative + fp_cumulative)
        points.append((recall, precision))

    # Backward precision envelope, then forward integration over recall steps.
    envelope = [0.0] * len(points)
    running = 0.0
    for index in range(len(points) - 1, -1, -1):
        running = max(running, points[index][1])
        envelope[index] = running

    ap = 0.0
    previous_recall = 0.0
    for index, (recall, _precision) in enumerate(points):
        if recall > previous_recall:
            ap += (recall - previous_recall) * envelope[index]
            previous_recall = recall
    return max(0.0, min(ap, 1.0))
