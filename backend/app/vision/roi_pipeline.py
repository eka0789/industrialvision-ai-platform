"""Multi-Zone ROI evaluation pipeline for Food Division SKU Recipes (Fase 1 & Enhancement 2).

Supports normalized (x, y, width, height) in [0.0, 1.0] as well as [x1, y1, x2, y2]
bounding boxes, and three specialized task types per zone:
- anomaly_segmentation (or seal_integrity): Top/bottom heat-seal check -> SEAL_DEFECT / seal_leak
- ocr_expiry (or expiry_code): Expiry date / Best Before / Lot code check via OCR -> MISSING_EXP_DATE / missing_exp_date
- object_detection (or product_quality): Bake color (HSV) + shape/foreign-object detection -> BURNT_OR_BROKEN / FOREIGN_MATTER
"""

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, model_validator

from app.vision.ocr import run_ocr
from app.vision.preprocessing import preprocess_food_frame

TASK_TYPE_TO_ZONE_TYPE = {
    "anomaly_segmentation": "seal_integrity",
    "ocr_expiry": "expiry_code",
    "object_detection": "product_quality",
    "seal_integrity": "seal_integrity",
    "expiry_code": "expiry_code",
    "product_quality": "product_quality",
}

ZONE_TYPE_TO_TASK_TYPE = {
    "seal_integrity": "anomaly_segmentation",
    "expiry_code": "ocr_expiry",
    "product_quality": "object_detection",
}

DEFAULT_DEFECT_BY_TASK = {
    "anomaly_segmentation": "SEAL_DEFECT",
    "ocr_expiry": "MISSING_EXP_DATE",
    "object_detection": "BURNT_OR_BROKEN",
}


class ROIZoneSpec(BaseModel):
    zone_id: str = Field(min_length=1, max_length=60)
    zone_name: str = Field(default="ROI Zone", min_length=1, max_length=120)
    name: str | None = None
    x: float = Field(default=0.05, ge=0.0, le=1.0)
    y: float = Field(default=0.05, ge=0.0, le=1.0)
    width: float = Field(default=0.90, gt=0.0, le=1.0)
    height: float = Field(default=0.20, gt=0.0, le=1.0)
    bbox: list[float] | None = None
    task_type: str = Field(default="object_detection")
    zone_type: str | None = None
    model_id: int | None = None
    confidence_threshold: float = Field(default=0.55, ge=0.01, le=0.99)
    defect_category: str = Field(default="SEAL_DEFECT", min_length=1, max_length=80)
    enabled: bool = True
    # Zone 1: Seal anomaly parameters
    anomaly_threshold: float = Field(default=0.58, ge=0.05, le=0.99)
    # Zone 2: OCR parameters
    ocr_required: bool = True
    ocr_regex: str = Field(
        default=r"(?:EXP|BB|BEST\s*BEFORE|LOT)?\s*\d{2}[/.\-]\d{2}[/.\-]\d{2,4}",
        max_length=240,
    )
    min_ocr_confidence: float = Field(default=55.0, ge=0.0, le=100.0)
    # Zone 3: HSV bake color & YOLO object detection parameters
    max_burnt_ratio: float = Field(default=0.18, ge=0.01, le=1.0)
    burnt_v_max: int = Field(default=55, ge=5, le=180)
    detection_confidence: float = Field(default=0.5, ge=0.05, le=0.99)
    reject_labels: list[str] = Field(
        default_factory=lambda: [
            "foreign_matter",
            "burnt",
            "crack",
            "contamination",
            "seal_defect",
            "missing_exp_date",
            "burnt_or_broken",
        ]
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_zone_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        # Synchronize zone_name <-> name
        if "zone_name" not in out and "name" in out and out["name"]:
            out["zone_name"] = out["name"]
        if "name" not in out or not out["name"]:
            out["name"] = out.get("zone_name", "ROI Zone")

        # Synchronize (x, y, width, height) <-> bbox [x1, y1, x2, y2]
        if "bbox" in out and isinstance(out["bbox"], (list, tuple)) and len(out["bbox"]) == 4:
            x1, y1, x2, y2 = [float(v) for v in out["bbox"]]
            if "x" not in data:
                out["x"] = round(x1, 4)
            if "y" not in data:
                out["y"] = round(y1, 4)
            if "width" not in data:
                out["width"] = round(x2 - x1, 4)
            if "height" not in data:
                out["height"] = round(y2 - y1, 4)
        else:
            x = float(out.get("x", 0.05))
            y = float(out.get("y", 0.05))
            w = float(out.get("width", 0.90))
            h = float(out.get("height", 0.20))
            out["bbox"] = [round(x, 4), round(y, 4), round(x + w, 4), round(y + h, 4)]

        # Synchronize task_type <-> zone_type
        raw_task = out.get("task_type")
        raw_zone = out.get("zone_type")
        if raw_zone and not raw_task:
            out["task_type"] = ZONE_TYPE_TO_TASK_TYPE.get(raw_zone, "object_detection")
        elif raw_task and not raw_zone:
            if raw_task not in TASK_TYPE_TO_ZONE_TYPE:
                raise ValueError(f"Unsupported task_type: {raw_task}")
            out["zone_type"] = TASK_TYPE_TO_ZONE_TYPE[raw_task]
        else:
            t = raw_task or "object_detection"
            if t not in TASK_TYPE_TO_ZONE_TYPE:
                raise ValueError(f"Unsupported task_type: {t}")
            out["task_type"] = ZONE_TYPE_TO_TASK_TYPE.get(t, t)
            out["zone_type"] = TASK_TYPE_TO_ZONE_TYPE[t]

        if "defect_category" not in out or not out["defect_category"]:
            out["defect_category"] = DEFAULT_DEFECT_BY_TASK.get(out["task_type"], "SEAL_DEFECT")

        if "confidence_threshold" in data and "anomaly_threshold" not in data and out["zone_type"] == "seal_integrity":
            out["anomaly_threshold"] = float(data["confidence_threshold"])
        if "confidence_threshold" in data and "detection_confidence" not in data:
            out["detection_confidence"] = float(data["confidence_threshold"])
        return out

    @model_validator(mode="after")
    def validate_geometry(self) -> "ROIZoneSpec":
        if not (0.0 <= self.x < 1.0 and 0.0 <= self.y < 1.0):
            raise ValueError("ROI x and y must be in [0.0, 1.0)")
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("ROI (x + width) and (y + height) must not exceed 1.0")
        self.bbox = [
            round(self.x, 4),
            round(self.y, 4),
            round(min(1.0, self.x + self.width), 4),
            round(min(1.0, self.y + self.height), 4),
        ]
        return self


def default_food_zones() -> list[dict[str, Any]]:
    """Return the standard 3-zone Food Division ROI layout."""
    return [
        ROIZoneSpec(
            zone_id="roi_1_seal",
            zone_name="ROI 1 — Area Atas (Heat-Seal Integrity / PatchCore)",
            x=0.05,
            y=0.02,
            width=0.90,
            height=0.20,
            task_type="anomaly_segmentation",
            confidence_threshold=0.58,
            defect_category="SEAL_DEFECT",
        ).model_dump(),
        ROIZoneSpec(
            zone_id="roi_2_expiry",
            zone_name="ROI 2 — Area Kode (Exp Date / Best Before OCR)",
            x=0.55,
            y=0.24,
            width=0.39,
            height=0.20,
            task_type="ocr_expiry",
            confidence_threshold=0.55,
            defect_category="MISSING_EXP_DATE",
            ocr_required=True,
            ocr_regex=r"(?:EXP|BB|LOT)?\s*\d{2}[/.\-]\d{2}[/.\-]\d{2,4}",
            min_ocr_confidence=55.0,
        ).model_dump(),
        ROIZoneSpec(
            zone_id="roi_3_product",
            zone_name="ROI 3 — Area Produk (HSV Bake Color + YOLOv8 Foreign Matter)",
            x=0.10,
            y=0.45,
            width=0.80,
            height=0.49,
            task_type="object_detection",
            confidence_threshold=0.50,
            defect_category="BURNT_OR_BROKEN",
            max_burnt_ratio=0.18,
            burnt_v_max=55,
        ).model_dump(),
    ]


def crop_roi(image: np.ndarray, bbox_or_spec: list[float] | ROIZoneSpec) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Crop a normalized [x1, y1, x2, y2] or ROIZoneSpec from image and return (crop, pixel_xyxy)."""
    if isinstance(bbox_or_spec, ROIZoneSpec):
        bbox = bbox_or_spec.bbox or [
            bbox_or_spec.x,
            bbox_or_spec.y,
            bbox_or_spec.x + bbox_or_spec.width,
            bbox_or_spec.y + bbox_or_spec.height,
        ]
    else:
        bbox = bbox_or_spec
    h, w = image.shape[:2]
    px1 = max(0, min(w - 1, int(round(bbox[0] * w))))
    py1 = max(0, min(h - 1, int(round(bbox[1] * h))))
    px2 = max(px1 + 1, min(w, int(round(bbox[2] * w))))
    py2 = max(py1 + 1, min(h, int(round(bbox[3] * h))))
    return image[py1:py2, px1:px2], (px1, py1, px2, py2)


def analyze_hsv_bake_quality(crop: np.ndarray, burnt_v_max: int = 55) -> dict[str, float]:
    """Compute HSV color histogram metrics to detect burnt/overbaked food regions."""
    arr = np.asarray(crop, dtype=np.uint8)
    if arr.size == 0:
        return {"burnt_ratio": 0.0, "mean_v": 0.0, "mean_s": 0.0}
    try:
        import cv2

        hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV) if arr.ndim == 3 else np.stack([arr, arr, arr], axis=-1)
        v_channel = hsv[:, :, 2]
        s_channel = hsv[:, :, 1]
    except Exception:
        v_channel = arr.max(axis=-1) if arr.ndim == 3 else arr
        s_channel = np.zeros_like(v_channel)

    burnt_mask = v_channel <= int(burnt_v_max)
    burnt_ratio = float(np.mean(burnt_mask))
    return {
        "burnt_ratio": round(burnt_ratio, 4),
        "mean_v": round(float(np.mean(v_channel)), 2),
        "mean_s": round(float(np.mean(s_channel)), 2),
    }


def evaluate_seal_zone(
    crop: np.ndarray,
    spec: ROIZoneSpec,
    override_anomaly_score: float | None = None,
) -> dict[str, Any]:
    """Evaluate heat-seal uniformity (PatchCore anomaly score or high-frequency seam discontinuity)."""
    if override_anomaly_score is not None:
        score = float(override_anomaly_score)
    else:
        arr = np.asarray(crop, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr.mean(axis=-1)
        row_diff = np.abs(np.diff(arr, axis=1)).mean() if arr.shape[1] > 1 else 0.0
        col_std = float(np.std(arr.mean(axis=0))) if arr.shape[1] > 1 else 0.0
        score = round(min(0.99, max(0.02, (col_std / 85.0) + (row_diff / 180.0))), 4)

    passed = score < spec.anomaly_threshold
    return {
        "zone_id": spec.zone_id,
        "zone_name": spec.zone_name,
        "name": spec.zone_name,
        "task_type": spec.task_type,
        "zone_type": spec.zone_type,
        "decision": "PASS" if passed else "FAIL",
        "defect_category": None if passed else "seal_leak",
        "defect_code": None if passed else spec.defect_category,
        "anomaly_score": score,
        "threshold": spec.anomaly_threshold,
        "reason": "Heat-seal uniform" if passed else f"Zone '{spec.zone_name}' FAILED ({spec.defect_category}): anomaly score {score:.3f} >= {spec.anomaly_threshold:.3f}",
    }


def evaluate_expiry_zone(
    crop: np.ndarray,
    spec: ROIZoneSpec,
    ocr_engine: Any | None = None,
    simulated_ocr_text: str | None = None,
) -> dict[str, Any]:
    """Evaluate expiry date / lot code presence and readability using OCR."""
    if simulated_ocr_text is not None:
        text = simulated_ocr_text.strip()
        confidence = 94.0 if text else 0.0
        status = "ok"
        mode = "simulated-ocr"
    else:
        ocr_res = run_ocr(crop, engine=ocr_engine)
        text = ocr_res.text
        confidence = ocr_res.confidence
        status = ocr_res.status
        mode = ocr_res.mode

    if status != "ok":
        if spec.ocr_required:
            return {
                "zone_id": spec.zone_id,
                "zone_name": spec.zone_name,
                "name": spec.zone_name,
                "task_type": spec.task_type,
                "zone_type": spec.zone_type,
                "decision": "ERROR",
                "defect_category": "missing_exp_date",
                "defect_code": spec.defect_category,
                "ocr_status": status,
                "ocr_mode": mode,
                "ocr_text": "",
                "ocr_confidence": None,
                "reason": f"Zone '{spec.zone_name}' ERROR: Mandatory Food Division OCR unavailable ({mode})",
            }
        return {
            "zone_id": spec.zone_id,
            "zone_name": spec.zone_name,
            "name": spec.zone_name,
            "task_type": spec.task_type,
            "zone_type": spec.zone_type,
            "decision": "REVIEW",
            "defect_category": None,
            "defect_code": None,
            "ocr_status": status,
            "ocr_mode": mode,
            "ocr_text": "",
            "ocr_confidence": None,
            "reason": "OCR optional and unavailable",
        }

    pattern_ok = bool(re.search(spec.ocr_regex, text, flags=re.IGNORECASE))
    conf_ok = confidence is None or confidence >= spec.min_ocr_confidence
    passed = bool(text and pattern_ok and conf_ok)

    return {
        "zone_id": spec.zone_id,
        "zone_name": spec.zone_name,
        "name": spec.zone_name,
        "task_type": spec.task_type,
        "zone_type": spec.zone_type,
        "decision": "PASS" if passed else "FAIL",
        "defect_category": None if passed else "missing_exp_date",
        "defect_code": None if passed else spec.defect_category,
        "ocr_status": status,
        "ocr_mode": mode,
        "ocr_text": text,
        "ocr_confidence": confidence,
        "reason": f"Expiry code verified: '{text}'" if passed else f"Zone '{spec.zone_name}' FAILED ({spec.defect_category}): expiry code missing/illegible ('{text}')",
    }


def evaluate_product_zone(
    crop: np.ndarray,
    spec: ROIZoneSpec,
    detections: list[dict[str, Any]] | None = None,
    pixel_bbox: tuple[int, int, int, int] = (0, 0, 100, 100),
) -> dict[str, Any]:
    """Evaluate product bake color (HSV) and foreign matter / broken shape (YOLO/ONNX)."""
    hsv_stats = analyze_hsv_bake_quality(crop, burnt_v_max=spec.burnt_v_max)
    is_burnt = hsv_stats["burnt_ratio"] > spec.max_burnt_ratio

    zone_detections: list[dict[str, Any]] = []
    foreign_failed = False
    reject_set = {lbl.casefold() for lbl in spec.reject_labels}

    for det in detections or []:
        label = str(det.get("label", "")).strip()
        conf = float(det.get("confidence", 0.0))
        zone_detections.append(det)
        if label.casefold() in reject_set and conf >= spec.detection_confidence:
            foreign_failed = True

    if foreign_failed:
        defect_cat: str | None = "foreign_matter"
        defect_code: str | None = spec.defect_category or "FOREIGN_MATTER"
        decision = "FAIL"
        reason = f"Zone '{spec.zone_name}' FAILED ({defect_code}): object defect detected in ROI"
    elif is_burnt:
        defect_cat = "burnt"
        defect_code = spec.defect_category or "BURNT_OR_BROKEN"
        decision = "FAIL"
        reason = f"Zone '{spec.zone_name}' FAILED ({defect_code}): burnt ratio {hsv_stats['burnt_ratio']:.2%} exceeds max {spec.max_burnt_ratio:.2%}"
    else:
        defect_cat = None
        defect_code = None
        decision = "PASS"
        reason = "Product color and shape within specification"

    return {
        "zone_id": spec.zone_id,
        "zone_name": spec.zone_name,
        "name": spec.zone_name,
        "task_type": spec.task_type,
        "zone_type": spec.zone_type,
        "decision": decision,
        "defect_category": defect_cat,
        "defect_code": defect_code,
        "hsv_metrics": hsv_stats,
        "detections": zone_detections,
        "pixel_bbox": list(pixel_bbox),
        "reason": reason,
    }


def evaluate_recipe_frame(
    image: np.ndarray,
    *,
    zones_data: list[dict[str, Any]] | None = None,
    preprocessing_data: dict[str, Any] | None = None,
    detections: list[dict[str, Any]] | None = None,
    override_seal_score: float | None = None,
    simulated_ocr_text: str | None = None,
    ocr_engine: Any | None = None,
    expiry_regex_override: str | None = None,
) -> dict[str, Any]:
    """Execute full Food Division pipeline: Preprocessing + Blur Check + Multi-Zone ROI."""
    start = time.perf_counter()
    prep = preprocess_food_frame(image, preprocessing_data)
    if prep["is_blurred"]:
        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
        rationale = prep.get("rationale") or (
            f"Camera lens obstructed/fogged or lighting failure — BLUR_ALERT: Laplacian variance {prep['laplacian_variance']:.1f} "
            f"< threshold {prep['blur_threshold']:.1f}"
        )
        return {
            "decision": "ERROR",
            "station_status": "degraded",
            "error_status": prep.get("quality_status", "LENS_BLUR_OR_FOG"),
            "reason": rationale,
            "decision_rationale": rationale,
            "preprocessing": {k: v for k, v in prep.items() if k != "image"},
            "zones": [],
            "defect_categories": [],
            "detections": [],
            "latency_ms": elapsed_ms,
        }

    proc_img = prep["image"]
    raw_zones = zones_data if zones_data is not None else default_food_zones()
    zone_results: list[dict[str, Any]] = []
    defect_categories: list[str] = []
    synthetic_detections: list[dict[str, Any]] = list(detections or [])

    for raw_zone in raw_zones:
        spec = ROIZoneSpec.model_validate(raw_zone)
        if expiry_regex_override and spec.zone_type == "expiry_code":
            spec.ocr_regex = expiry_regex_override
        if not spec.enabled:
            continue
        crop, px_bbox = crop_roi(proc_img, spec)
        if spec.zone_type == "seal_integrity":
            res = evaluate_seal_zone(crop, spec, override_anomaly_score=override_seal_score)
        elif spec.zone_type == "expiry_code":
            res = evaluate_expiry_zone(
                crop, spec, ocr_engine=ocr_engine, simulated_ocr_text=simulated_ocr_text
            )
        else:
            res = evaluate_product_zone(crop, spec, detections=detections, pixel_bbox=px_bbox)

        res["x"] = spec.x
        res["y"] = spec.y
        res["width"] = spec.width
        res["height"] = spec.height
        res["bbox"] = spec.bbox
        res["pixel_bbox"] = list(px_bbox)
        zone_results.append(res)
        if res.get("defect_category"):
            cat = res["defect_category"]
            code = res.get("defect_code") or cat.upper()
            if cat not in defect_categories:
                defect_categories.append(cat)
            if code not in defect_categories:
                defect_categories.append(code)
            synthetic_detections.append(
                {
                    "label": code,
                    "defect_category": cat,
                    "confidence": 0.95,
                    "bbox": [float(px_bbox[0]), float(px_bbox[1]), float(px_bbox[2]), float(px_bbox[3])],
                    "zone_id": spec.zone_id,
                    "zone_name": spec.zone_name,
                }
            )

    decisions = [z["decision"] for z in zone_results]
    if "ERROR" in decisions:
        overall = "ERROR"
        reason = "; ".join(z["reason"] for z in zone_results if z["decision"] == "ERROR")
    elif "FAIL" in decisions:
        overall = "FAIL"
        reason = "; ".join(z["reason"] for z in zone_results if z["decision"] == "FAIL")
    elif "REVIEW" in decisions or not zone_results:
        overall = "REVIEW"
        reason = "One or more zones require operator review"
    else:
        overall = "PASS"
        reason = "All Multi-Zone ROI checks passed (Seal + Expiry OCR + Product Quality)"

    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
    seal_scores = [z["anomaly_score"] for z in zone_results if "anomaly_score" in z]
    return {
        "decision": overall,
        "station_status": "connected",
        "error_status": "ocr_unavailable" if overall == "ERROR" else None,
        "reason": reason,
        "decision_rationale": reason,
        "preprocessing": {k: v for k, v in prep.items() if k != "image"},
        "zones": zone_results,
        "defect_categories": defect_categories,
        "detections": synthetic_detections,
        "anomaly_score": max(seal_scores) if seal_scores else None,
        "latency_ms": elapsed_ms,
    }
