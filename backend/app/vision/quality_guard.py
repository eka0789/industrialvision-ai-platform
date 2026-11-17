"""Camera Lens Quality & Factory Environment Guard (Fase 3: `quality_guard.py`).

Food manufacturing lines experience steam condensation, oil splatter, and IP69K
sanitation washdowns, as well as inspection lamp degradation. Before running AI
inference, this module evaluates frame quality in < 5 ms:
1. Sharpness / Fog Score: Laplacian Variance (< blur_threshold -> LENS_BLUR_OR_FOG).
2. Illumination Shift: Mean & Std of luminance (dead lamp, obstruction, or extreme
   saturation -> LIGHTING_FAILURE / LENS_BLUR_OR_FOG).

Fail-safe rule: When LENS_BLUR_OR_FOG or LIGHTING_FAILURE is detected, the pipeline
NEVER returns a false PASS — it returns decision="ERROR" with rationale
"Camera lens obstructed/fogged or lighting failure", transitions Camera.status to
DEGRADED, and emits real-time WebSocket + Prometheus alerts.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

FAILURE_RATIONALE = "Camera lens obstructed/fogged or lighting failure"


def compute_sharpness_and_illumination(image: np.ndarray) -> tuple[float, float, float]:
    """Compute (laplacian_variance, mean_luminance, std_luminance) in < 5 ms."""
    arr = np.asarray(image)
    if arr.ndim == 3:
        # Fast integer-weighted luminance approximation (BGR -> Y)
        gray = (0.114 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.299 * arr[:, :, 2]).astype(np.float64)
    elif arr.ndim == 2:
        gray = arr.astype(np.float64)
    else:
        raise ValueError("Image must be a 2D grayscale or 3D BGR/RGB numpy array")

    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0, 0.0, 0.0

    mean_lum = round(float(np.mean(gray)), 2)
    std_lum = round(float(np.std(gray)), 2)

    try:
        import cv2

        lap = cv2.Laplacian(gray, cv2.CV_64F)
        lap_var = round(float(lap.var()), 3)
    except Exception:
        center = gray[1:-1, 1:-1]
        lap = gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:] - 4.0 * center
        lap_var = round(float(np.var(lap)), 3)

    return lap_var, mean_lum, std_lum


def evaluate_frame_quality(
    image: np.ndarray,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate lens sharpness (Laplacian variance) and illumination stability (< 5 ms)."""
    started = time.perf_counter()
    cfg = config_data or {}

    blur_enabled = bool(cfg.get("blur_detection_enabled", True))
    blur_threshold = float(cfg.get("blur_threshold", 80.0))
    illumination_enabled = bool(cfg.get("illumination_guard_enabled", True))
    min_luminance = float(cfg.get("min_luminance", 15.0))
    max_luminance = float(cfg.get("max_luminance", 248.0))
    min_contrast_std = float(cfg.get("min_contrast_std", 2.5))

    lap_var, mean_lum, std_lum = compute_sharpness_and_illumination(image)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    is_blurred = bool(blur_enabled and lap_var < blur_threshold)
    is_lighting_failed = bool(
        illumination_enabled
        and (mean_lum < min_luminance or mean_lum > max_luminance or std_lum < min_contrast_std)
    )

    if is_lighting_failed and (mean_lum < min_luminance or mean_lum > max_luminance):
        status = "LIGHTING_FAILURE"
        detail = (
            f"Illumination out of bounds (mean={mean_lum:.1f}, allowed=[{min_luminance:.1f}, {max_luminance:.1f}], "
            f"std={std_lum:.1f}, laplacian={lap_var:.1f})"
        )
    elif is_blurred or is_lighting_failed:
        status = "LENS_BLUR_OR_FOG"
        detail = (
            f"BLUR_ALERT: Laplacian variance {lap_var:.1f} < threshold {blur_threshold:.1f} "
            f"(mean_lum={mean_lum:.1f}, std_lum={std_lum:.1f})"
        )
    else:
        status = "OK"
        detail = f"Lens sharp (laplacian={lap_var:.1f}) and illumination nominal (mean={mean_lum:.1f})"

    is_obstructed = status != "OK"
    return {
        "status": status,
        "legacy_status": "BLUR_ALERT" if is_obstructed else "OK",
        "is_obstructed": is_obstructed,
        "is_blurred": is_obstructed,
        "lighting_ok": not is_lighting_failed,
        "sharpness_score": lap_var,
        "laplacian_variance": lap_var,
        "blur_threshold": blur_threshold,
        "mean_luminance": mean_lum,
        "std_luminance": std_lum,
        "min_luminance": min_luminance,
        "max_luminance": max_luminance,
        "elapsed_ms": elapsed_ms,
        "decision_if_failed": "ERROR" if is_obstructed else None,
        "rationale": f"{FAILURE_RATIONALE} — {detail}" if is_obstructed else detail,
        "short_rationale": FAILURE_RATIONALE if is_obstructed else "OK",
    }


def apply_camera_degraded_alert(
    db,
    camera,
    quality_result: dict[str, Any],
    *,
    inspection_id: int | None = None,
) -> None:
    """Transition camera to DEGRADED and emit WebSocket + Prometheus alerts when quality_guard trips."""
    from app.core.constants import CameraStatus
    from app.db.models import SystemEvent
    from app.services.event_bus import event_bus
    from app.services.live_feed import inspection_topic
    from app.services.metrics import record_decision, record_quality_guard

    record_quality_guard(
        camera_id=camera.id,
        status=quality_result["status"],
        sharpness_score=quality_result["sharpness_score"],
    )

    if not quality_result["is_obstructed"]:
        return

    camera.status = CameraStatus.DEGRADED
    camera.last_error = quality_result["rationale"]
    db.add(
        SystemEvent(
            org_id=camera.org_id,
            event_type="camera.blur_alert" if quality_result["status"] == "LENS_BLUR_OR_FOG" else "camera.lighting_alert",
            level="warning",
            message=f"{quality_result['status']} on {camera.name}: {quality_result['rationale']}",
            metadata_json={
                "camera_id": camera.id,
                "inspection_id": inspection_id,
                "status": quality_result["status"],
                "sharpness_score": quality_result["sharpness_score"],
                "laplacian_variance": quality_result["laplacian_variance"],
                "mean_luminance": quality_result["mean_luminance"],
                "std_luminance": quality_result["std_luminance"],
            },
        )
    )
    record_decision("ERROR", camera_id=camera.id)
    try:
        event_bus.publish(
            inspection_topic(camera.org_id),
            {
                "type": "camera.quality_alert",
                "org_id": camera.org_id,
                "camera_id": camera.id,
                "camera_status": CameraStatus.DEGRADED,
                "quality_status": quality_result["status"],
                "decision": "ERROR",
                "decision_rationale": FAILURE_RATIONALE,
                "detail": quality_result["rationale"],
                "sharpness_score": quality_result["sharpness_score"],
                "mean_luminance": quality_result["mean_luminance"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        pass
