"""Anomaly evaluation metrics: image/pixel AUROC and localization.

Ground-truth compatible fixtures required. No real dataset assumed.
Dataset licenses (e.g., MVTec AD CC BY-NC-SA 4.0) documented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ImageLevelMetrics:
    auroc: float
    auprc: float
    f1_optimal: float
    threshold_at_f1: float


@dataclass(frozen=True)
class PixelLevelMetrics:
    auroc: float
    auprc: float
    f1_optimal: float
    threshold_at_f1: float
    pro: float  # Per-Region Overlap


@dataclass(frozen=True)
class EvaluationResult:
    image_level: ImageLevelMetrics
    pixel_level: PixelLevelMetrics | None
    samples: int


def _trapezoidal_auc(x: np.ndarray, y: np.ndarray) -> float:
    """Trapezoidal rule for AUC."""
    order = np.argsort(x)
    x_sorted = x[order]
    y_sorted = y[order]
    return float(np.trapezoid(y_sorted, x_sorted))


def _roc_curve(y_true: np.ndarray, y_score: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute ROC curve points (FPR, TPR, thresholds)."""
    # Sort by score descending
    order = np.argsort(-y_score)
    y_true = y_true[order]
    y_score = y_score[order]

    n_pos = np.sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.array([1.0, 0.0])

    tps = np.cumsum(y_true)
    fps = np.cumsum(~y_true)

    tpr = tps / n_pos
    fpr = fps / n_neg

    # Add (0,0) and (1,1)
    fpr = np.concatenate([[0.0], fpr, [1.0]])
    tpr = np.concatenate([[0.0], tpr, [1.0]])
    thresholds = np.concatenate([[y_score[0] + 1], y_score, [y_score[-1] - 1]])

    return fpr, tpr, thresholds


def _pr_curve(y_true: np.ndarray, y_score: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Precision-Recall curve."""
    order = np.argsort(-y_score)
    y_true = y_true[order]
    y_score = y_score[order]

    tps = np.cumsum(y_true)
    fps = np.cumsum(~y_true)
    precision = tps / (tps + fps + 1e-12)
    recall = tps / (np.sum(y_true) + 1e-12)

    return recall, precision, y_score


def compute_image_auroc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Image-level AUROC for anomaly detection."""
    if y_true.shape != y_score.shape:
        raise ValueError("Shape mismatch between labels and scores")
    fpr, tpr, _ = _roc_curve(y_true, y_score)
    return _trapezoidal_auc(fpr, tpr)


def compute_pixel_auroc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Pixel-level AUROC. y_true: (N, H, W) bool; y_score: (N, H, W) float."""
    y_true_flat = y_true.flatten().astype(bool)
    y_score_flat = y_score.flatten()
    return compute_image_auroc(y_true_flat, y_score_flat)


def compute_f1_at_threshold(
    y_true: np.ndarray, y_score: np.ndarray, threshold: float
) -> float:
    """F1 score at specific threshold."""
    y_pred = y_score >= threshold
    tp = np.sum(y_true & y_pred)
    fp = np.sum(~y_true & y_pred)
    fn = np.sum(y_true & ~y_pred)
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def compute_optimal_f1(y_true: np.ndarray, y_score: np.ndarray) -> tuple[float, float]:
    """Find threshold that maximizes F1."""
    fpr, tpr, thresholds = _roc_curve(y_true, y_score)
    f1_scores = []
    for t in thresholds:
        y_pred = y_score >= t
        tp = np.sum(y_true & y_pred)
        fp = np.sum(~y_true & y_pred)
        fn = np.sum(y_true & ~y_pred)
        if tp == 0:
            f1_scores.append(0.0)
        else:
            prec = tp / (tp + fp)
            rec = tp / (tp + fn)
            f1_scores.append(2 * prec * rec / (prec + rec))
    best_idx = int(np.argmax(f1_scores))
    return float(f1_scores[best_idx]), float(thresholds[best_idx])


def compute_pro(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Per-Region Overlap (PRO) for localization.
    
    y_true: (N, H, W) bool masks; y_score: (N, H, W) float heatmaps.
    Computes mean overlap of thresholded regions vs ground truth.
    """
    if y_true.ndim != 3 or y_score.ndim != 3:
        raise ValueError("PRO requires 3D arrays (N, H, W)")
    if y_true.shape != y_score.shape:
        raise ValueError("Shape mismatch")

    pros = []
    for mask, score_map in zip(y_true, y_score):
        if not mask.any():
            continue
        # Threshold at various levels and compute overlap
        thresholds = np.linspace(0, 1, 100)
        for t in thresholds:
            pred = score_map >= t
            if pred.any():
                overlap = np.sum(mask & pred) / np.sum(pred)
                pros.append(overlap)
    return float(np.mean(pros)) if pros else 0.0


def evaluate_anomaly(
    y_true_image: np.ndarray,
    y_score_image: np.ndarray,
    y_true_pixel: np.ndarray | None = None,
    y_score_pixel: np.ndarray | None = None,
) -> EvaluationResult:
    """Full anomaly evaluation with image and optional pixel metrics.
    
    Args:
        y_true_image: (N,) bool — image-level anomaly labels
        y_score_image: (N,) float — image-level anomaly scores
        y_true_pixel: (N, H, W) bool — pixel-level masks (optional)
        y_score_pixel: (N, H, W) float — pixel-level heatmaps (optional)
    
    Returns:
        EvaluationResult with image/pixel AUROC, AUPRC, F1, PRO
    """
    if len(y_true_image) != len(y_score_image):
        raise ValueError("Image labels and scores length mismatch")

    # Image-level metrics
    img_auroc = compute_image_auroc(y_true_image, y_score_image)
    recall, precision, _ = _pr_curve(y_true_image, y_score_image)
    img_auprc = _trapezoidal_auc(recall, precision)
    f1_img, th_img = compute_optimal_f1(y_true_image, y_score_image)

    img_metrics = ImageLevelMetrics(
        auroc=img_auroc,
        auprc=img_auprc,
        f1_optimal=f1_img,
        threshold_at_f1=th_img,
    )

    # Pixel-level metrics (if provided)
    pixel_metrics = None
    if y_true_pixel is not None and y_score_pixel is not None:
        pix_auroc = compute_pixel_auroc(y_true_pixel, y_score_pixel)
        recall_p, precision_p, _ = _pr_curve(y_true_pixel.flatten(), y_score_pixel.flatten())
        pix_auprc = _trapezoidal_auc(recall_p, precision_p)
        f1_pix, th_pix = compute_optimal_f1(y_true_pixel.flatten(), y_score_pixel.flatten())
        pro = compute_pro(y_true_pixel, y_score_pixel)
        pixel_metrics = PixelLevelMetrics(
            auroc=pix_auroc,
            auprc=pix_auprc,
            f1_optimal=f1_pix,
            threshold_at_f1=th_pix,
            pro=pro,
        )

    return EvaluationResult(
        image_level=img_metrics,
        pixel_level=pixel_metrics,
        samples=len(y_true_image),
    )


# Synthetic fixtures for testing (no dataset licensing issues)
def generate_synthetic_anomaly_data(
    n_normal: int = 50,
    n_anomaly: int = 50,
    img_size: tuple[int, int] = (32, 32),
    seed: int = 42,
) -> tuple[
    np.ndarray, np.ndarray,  # y_true_image, y_score_image
    np.ndarray, np.ndarray,  # y_true_pixel, y_score_pixel
]:
    """Generate synthetic normal/anomaly data for metric testing."""
    rng = np.random.default_rng(seed)
    n_total = n_normal + n_anomaly

    y_true_image = np.zeros(n_total, dtype=bool)
    y_true_image[n_normal:] = True

    y_score_image = np.zeros(n_total)
    y_score_image[:n_normal] = rng.beta(0.5, 5, n_normal)  # low scores for normal
    y_score_image[n_normal:] = rng.beta(5, 0.5, n_anomaly)  # high scores for anomaly

    # Pixel-level: anomaly region in center
    H, W = img_size
    y_true_pixel = np.zeros((n_total, H, W), dtype=bool)
    y_score_pixel = np.zeros((n_total, H, W), dtype=float)
    # Use even division for consistent slice sizes
    h_mid = H // 2
    w_mid = W // 2
    h_half = H // 4
    w_half = W // 4
    y_start = h_mid - h_half
    y_end = h_mid + h_half
    x_start = w_mid - w_half
    x_end = w_mid + w_half
    h_region = y_end - y_start
    w_region = x_end - x_start

    for i in range(n_anomaly):
        idx = n_normal + i
        y_true_pixel[idx, y_start:y_end, x_start:x_end] = True
        y_score_pixel[idx, :, :] = rng.beta(2, 5, (H, W))
        y_score_pixel[idx, y_start:y_end, x_start:x_end] = rng.beta(5, 1, (h_region, w_region))
    for i in range(n_normal):
        y_score_pixel[i, :, :] = rng.beta(1, 5, (H, W))

    return y_true_image, y_score_image, y_true_pixel, y_score_pixel


def _demo() -> None:
    """Quick demo with synthetic data."""
    y_ti, y_si, y_tp, y_sp = generate_synthetic_anomaly_data()
    result = evaluate_anomaly(y_ti, y_si, y_tp, y_sp)
    print(f"Image AUROC: {result.image_level.auroc:.3f}")
    print(f"Image AUPRC: {result.image_level.auprc:.3f}")
    print(f"Image F1@opt: {result.image_level.f1_optimal:.3f} (th={result.image_level.threshold_at_f1:.3f})")
    if result.pixel_level:
        print(f"Pixel AUROC: {result.pixel_level.auroc:.3f}")
        print(f"Pixel PRO: {result.pixel_level.pro:.3f}")


if __name__ == "__main__":
    _demo()