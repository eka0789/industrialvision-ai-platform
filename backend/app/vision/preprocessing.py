"""Food-Grade Image Preprocessing Pipeline (Fase 3: `preprocessing.py`).

Deterministic OpenCV/NumPy image conditioning pipeline driven by `preprocessing_config`
on `InspectionRecipe`:
1. Glare / Specular Reflection Suppression: CLAHE on the Luminance (L) channel in LAB
   color space to suppress factory lamp reflections on glossy plastic and aluminum foil
   without distorting food hue.
2. Gamma & White Balance Normalization: Gray-World chromatic adaptation + LUT Gamma
   correction to prevent day/night shift lighting shifts from causing false color alarms.
3. HSV Color Masking (Optional per Recipe): Color-range filter to inspect bake maturity /
   charred regions on bakery and snack products.
4. Lens & Illumination Guard: Delegates to `app.vision.quality_guard` (< 5 ms).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.vision.quality_guard import compute_sharpness_and_illumination, evaluate_frame_quality


@dataclass(frozen=True)
class PreprocessingConfig:
    enabled: bool = True
    clahe_enabled: bool = True
    clahe_clip_limit: float = 2.0
    clahe_tile_size: int = 8
    white_balance_enabled: bool = True
    gamma: float = 1.2
    blur_detection_enabled: bool = True
    blur_threshold: float = 80.0
    illumination_guard_enabled: bool = True
    min_luminance: float = 15.0
    max_luminance: float = 248.0
    min_contrast_std: float = 2.5
    hsv_mask_enabled: bool = False
    hsv_lower: tuple[int, int, int] = field(default=(10, 40, 50))
    hsv_upper: tuple[int, int, int] = field(default=(35, 255, 245))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PreprocessingConfig":
        if not data:
            return cls()
        raw_lower = data.get("hsv_lower", (10, 40, 50))
        raw_upper = data.get("hsv_upper", (35, 255, 245))
        lower = (int(raw_lower[0]), int(raw_lower[1]), int(raw_lower[2])) if isinstance(raw_lower, (list, tuple)) and len(raw_lower) == 3 else (10, 40, 50)
        upper = (int(raw_upper[0]), int(raw_upper[1]), int(raw_upper[2])) if isinstance(raw_upper, (list, tuple)) and len(raw_upper) == 3 else (35, 255, 245)
        return cls(
            enabled=bool(data.get("enabled", True)),
            clahe_enabled=bool(data.get("clahe_enabled", True)),
            clahe_clip_limit=float(data.get("clahe_clip_limit", 2.0)),
            clahe_tile_size=max(2, int(data.get("clahe_tile_size", 8))),
            white_balance_enabled=bool(data.get("white_balance_enabled", True)),
            gamma=max(0.2, min(4.0, float(data.get("gamma", 1.2)))),
            blur_detection_enabled=bool(data.get("blur_detection_enabled", True)),
            blur_threshold=max(0.0, float(data.get("blur_threshold", 80.0))),
            illumination_guard_enabled=bool(data.get("illumination_guard_enabled", True)),
            min_luminance=float(data.get("min_luminance", 15.0)),
            max_luminance=float(data.get("max_luminance", 248.0)),
            min_contrast_std=float(data.get("min_contrast_std", 2.5)),
            hsv_mask_enabled=bool(data.get("hsv_mask_enabled", False)),
            hsv_lower=lower,
            hsv_upper=upper,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "clahe_enabled": self.clahe_enabled,
            "clahe_clip_limit": self.clahe_clip_limit,
            "clahe_tile_size": self.clahe_tile_size,
            "white_balance_enabled": self.white_balance_enabled,
            "gamma": self.gamma,
            "blur_detection_enabled": self.blur_detection_enabled,
            "blur_threshold": self.blur_threshold,
            "illumination_guard_enabled": self.illumination_guard_enabled,
            "min_luminance": self.min_luminance,
            "max_luminance": self.max_luminance,
            "min_contrast_std": self.min_contrast_std,
            "hsv_mask_enabled": self.hsv_mask_enabled,
            "hsv_lower": list(self.hsv_lower),
            "hsv_upper": list(self.hsv_upper),
        }


def compute_laplacian_variance(image: np.ndarray) -> float:
    """Compute variance of the Laplacian operator to detect lens fog/condensation/washdown blur."""
    lap_var, _, _ = compute_sharpness_and_illumination(image)
    return lap_var


def apply_white_balance(image: np.ndarray) -> np.ndarray:
    """Gray-World automatic white balance normalization to stabilize inter-shift lighting."""
    arr = np.asarray(image, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 3:
        return arr
    float_img = arr.astype(np.float32)
    means = float_img.mean(axis=(0, 1))
    gray_mean = float(means.mean())
    if gray_mean < 1.0:
        return arr
    scales = np.where(means > 1.0, gray_mean / means, 1.0)
    # Clamp channel gains to [0.65, 1.45] so saturated single-hue foods are not distorted
    scales = np.clip(scales, 0.65, 1.45)
    balanced = np.clip(float_img * scales.reshape((1, 1, 3)), 0.0, 255.0).astype(np.uint8)
    return balanced


def apply_gamma_correction(image: np.ndarray, gamma: float = 1.2) -> np.ndarray:
    """Apply LUT gamma correction to normalize brightness and suppress specular glare."""
    if abs(gamma - 1.0) < 1e-3:
        return np.asarray(image, dtype=np.uint8)
    inv_gamma = 1.0 / max(0.1, float(gamma))
    lut = np.array([((i / 255.0) ** inv_gamma) * 255.0 for i in range(256)], dtype=np.uint8)
    return lut[np.asarray(image, dtype=np.uint8)]


def apply_clahe(image: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    """Apply CLAHE on the Luminance (L) channel in LAB color space to suppress foil/plastic glare."""
    arr = np.asarray(image, dtype=np.uint8)
    try:
        import cv2

        grid = (max(2, int(tile_size)), max(2, int(tile_size)))
        clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=grid)
        if arr.ndim == 2:
            return clahe.apply(arr)
        lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)
        l_eq = clahe.apply(l_chan)
        merged = cv2.merge((l_eq, a_chan, b_chan))
        return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    except Exception:
        return arr


def apply_hsv_color_mask(
    image: np.ndarray,
    hsv_lower: tuple[int, int, int] = (10, 40, 50),
    hsv_upper: tuple[int, int, int] = (35, 255, 245),
) -> dict[str, Any]:
    """Filter HSV color range to evaluate bakery/snack bake maturity vs burnt/undercooked regions."""
    arr = np.asarray(image, dtype=np.uint8)
    if arr.size == 0 or arr.ndim != 3:
        return {"mask": np.zeros(arr.shape[:2], dtype=np.uint8), "in_range_ratio": 0.0, "burnt_ratio": 0.0}
    try:
        import cv2

        hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
        lower_np = np.array(hsv_lower, dtype=np.uint8)
        upper_np = np.array(hsv_upper, dtype=np.uint8)
        mask = cv2.inRange(hsv, lower_np, upper_np)
        burnt_mask = hsv[:, :, 2] <= 55
    except Exception:
        mask = np.ones(arr.shape[:2], dtype=np.uint8) * 255
        burnt_mask = arr.max(axis=-1) <= 55

    in_range_ratio = round(float(np.mean(mask > 0)), 4)
    burnt_ratio = round(float(np.mean(burnt_mask)), 4)
    return {
        "mask": mask,
        "in_range_ratio": in_range_ratio,
        "burnt_ratio": burnt_ratio,
    }


def preprocess_food_frame(
    image: np.ndarray,
    config_data: dict[str, Any] | PreprocessingConfig | None = None,
) -> dict[str, Any]:
    """Run quality_guard (< 5 ms) + White Balance + Gamma + CLAHE + optional HSV masking."""
    cfg = config_data if isinstance(config_data, PreprocessingConfig) else PreprocessingConfig.from_dict(config_data)
    cfg_dict = cfg.to_dict()
    arr = np.asarray(image, dtype=np.uint8)

    quality = evaluate_frame_quality(arr, cfg_dict)
    is_obstructed = quality["is_obstructed"]

    processed = arr
    hsv_in_range_ratio: float | None = None
    if cfg.enabled and not is_obstructed:
        if cfg.white_balance_enabled:
            processed = apply_white_balance(processed)
        if abs(cfg.gamma - 1.0) >= 1e-3:
            processed = apply_gamma_correction(processed, cfg.gamma)
        if cfg.clahe_enabled:
            processed = apply_clahe(processed, cfg.clahe_clip_limit, cfg.clahe_tile_size)
        if cfg.hsv_mask_enabled:
            hsv_out = apply_hsv_color_mask(processed, cfg.hsv_lower, cfg.hsv_upper)
            hsv_in_range_ratio = hsv_out["in_range_ratio"]

    return {
        "image": processed,
        "laplacian_variance": quality["laplacian_variance"],
        "sharpness_score": quality["sharpness_score"],
        "blur_threshold": cfg.blur_threshold,
        "mean_luminance": quality["mean_luminance"],
        "std_luminance": quality["std_luminance"],
        "lighting_ok": quality["lighting_ok"],
        "is_blurred": is_obstructed,
        "is_obstructed": is_obstructed,
        "quality_status": quality["status"],
        "status": "BLUR_ALERT" if is_obstructed else "OK",
        "rationale": quality["rationale"],
        "short_rationale": quality["short_rationale"],
        "clahe_applied": bool(cfg.enabled and cfg.clahe_enabled and not is_obstructed),
        "white_balance_applied": bool(cfg.enabled and cfg.white_balance_enabled and not is_obstructed),
        "gamma": cfg.gamma if cfg.enabled else 1.0,
        "hsv_mask_enabled": cfg.hsv_mask_enabled,
        "hsv_in_range_ratio": hsv_in_range_ratio,
        "quality_elapsed_ms": quality["elapsed_ms"],
    }
