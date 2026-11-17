"""PdM inference service (B4) — fail-safe predictions from registry artifacts.

Response contract (never a fabricated number):

- ``mode: "model"`` — prediction from the registered, checksum-verified
  sklearn artifact.
- ``mode: "rules-only-fallback"`` — no registry artifact for the org; the
  documented AI4I mirror thresholds decide (explicitly labeled, surfaced in
  the API and UI).
- ``mode: "error"`` — unreadable/corrupt artifact or non-finite features;
  ``error`` explains why. Never silently degrades to rules from a broken
  model: a corrupt artifact is an ERROR, not a fallback.

Health score derivation (documented): ``health = 1 - failure_probability``
clamped to [0, 1] in model mode; in rules mode health is 1.0 when no
documented threshold trips, 0.5 when one trips, 0.0 when two or more trip.
The recommendation maps health/priority: >=0.75 "no action", >=0.5 "schedule
inspection", >=0.25 "plan maintenance", else "immediate attention".
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.pdm.dataset import (
    HDF_DELTA_K,
    HDF_RPM,
    OSF_THRESHOLD_BY_TYPE,
    PWF_MAX_W,
    PWF_MIN_W,
    TWF_MAX_MIN,
    TWF_MIN_MIN,
)
from app.db.models import FEATURE_NAMES

logger = logging.getLogger(__name__)

MODE_MODEL = "model"
MODE_RULES = "rules-only-fallback"
MODE_ERROR = "error"


def _load_active_artifact(db, org_id: int, task_type: str) -> tuple[dict, Path] | None:
    """Load the org's active PdM artifact via the verified-checksum path."""
    from app.db.models import ModelVersion

    row = (
        db.query(ModelVersion)
        .filter(ModelVersion.org_id == org_id, ModelVersion.task_type == task_type, ModelVersion.is_active.is_(True))
        .first()
    )
    if row is None:
        return None
    path = Path(settings.MODEL_ROOT) / (row.source_path or "")
    if not path.is_file():
        raise FileNotFoundError(f"Registered artifact missing on disk: {row.source_path}")
    if row.checksum:
        from app.services.model_registry import checksum_artifact

        actual = checksum_artifact(path)
        if actual != row.checksum:
            raise ValueError(f"Artifact checksum mismatch for {row.name}: artifact may be corrupt or replaced")
    payload = joblib_load(path)
    return payload, path


def joblib_load(path: Path) -> dict:
    import joblib

    return joblib.load(path)


def _rules_failure_modes(features: dict[str, float]) -> list[str]:
    """Documented AI4I mirror thresholds → triggered failure modes."""
    modes: list[str] = []
    air = features["air_temperature_k"]
    process = features["process_temperature_k"]
    rpm = features["rotational_speed_rpm"]
    torque = features["torque_nm"]
    wear = features["tool_wear_min"]
    if TWF_MIN_MIN <= wear <= TWF_MAX_MIN:
        modes.append("TWF")
    if (process - air) < HDF_DELTA_K and rpm < HDF_RPM:
        modes.append("HDF")
    power = torque * rpm * 2 * math.pi / 60
    if power < PWF_MIN_W or power > PWF_MAX_W:
        modes.append("PWF")
    # Rules mode has no product grade; use the strictest documented threshold.
    if wear * torque > OSF_THRESHOLD_BY_TYPE["H"]:
        modes.append("OSF")
    return modes


def _food_failure_modes(food_metrics: dict[str, float | None]) -> list[str]:
    """Food Division packaging & thermal machine failure modes (Fase 5)."""
    from app.pdm.schemas import evaluate_food_equipment_anomalies

    return evaluate_food_equipment_anomalies(food_metrics)


def correlate_vision_pdm(db, machine, failure_modes: list[str], food_metrics: dict[str, float | None]) -> dict[str, Any]:
    """Cross-correlate machine sensor anomalies (e.g. SEAL_TEMP_LOW) with Vision AI defects (SEAL_DEFECT / seal_leak)."""
    from app.services.correlation_engine import evaluate_vision_pdm_correlation

    del failure_modes, food_metrics
    if not machine.camera_id:
        return {
            "correlated": False,
            "camera_id": None,
            "recent_fail_count": 0,
            "seal_leak_count": 0,
            "seal_defect_count": 0,
            "diagnosis": None,
        }
    return evaluate_vision_pdm_correlation(
        db,
        org_id=machine.org_id,
        camera_id=machine.camera_id,
        machine_id=machine.id,
        window_minutes=5,
        min_seal_defects=1,
        auto_create_request=False,
    )


def _recommendation(health: float) -> str:
    if health >= 0.75:
        return "no action"
    if health >= 0.5:
        return "schedule inspection"
    if health >= 0.25:
        return "plan maintenance"
    return "immediate attention"


def _finite_features(values: list[float]) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in values)


def predict_for_machine(db, machine) -> dict[str, Any]:
    """Predict failure probability / RUL / health for a machine's latest reading.

    Reads the machine's most recent sensor sample; fails safe per contract.
    """
    from app.db.models import SensorReading

    latest = (
        db.query(SensorReading)
        .filter(SensorReading.machine_id == machine.id)
        .order_by(SensorReading.recorded_at.desc(), SensorReading.id.desc())
        .first()
    )
    if latest is None:
        return {
            "mode": MODE_ERROR,
            "machine_id": machine.id,
            "error": "no sensor readings available for prediction",
            "failure_probability": None,
            "rul_minutes": None,
            "health": None,
            "failure_modes": [],
            "recommendation": "collect sensor data",
        }

    features = dict(zip(FEATURE_NAMES, latest.features()))
    food_metrics = latest.food_features() if hasattr(latest, "food_features") else {}
    result = predict_features(db, org_id=machine.org_id, features=features)

    food_modes = _food_failure_modes(food_metrics)
    if food_modes and result.get("mode") != MODE_ERROR:
        combined_modes = list(result.get("failure_modes") or [])
        for fm in food_modes:
            if fm not in combined_modes:
                combined_modes.append(fm)
        result["failure_modes"] = combined_modes
        if result.get("mode") == MODE_RULES:
            result["health"] = 1.0 if not combined_modes else (0.5 if len(combined_modes) == 1 else 0.0)
            result["recommendation"] = _recommendation(result["health"])
        elif result.get("health") is not None:
            result["health"] = round(min(float(result["health"]), 0.35 if len(food_modes) == 1 else 0.1), 4)
            result["recommendation"] = _recommendation(result["health"])

    result["machine_id"] = machine.id
    result["reading_id"] = latest.id
    result["features"] = features
    result["food_features"] = food_metrics
    result["vision_correlation"] = correlate_vision_pdm(
        db, machine, list(result.get("failure_modes") or []), food_metrics
    )
    return result


def predict_features(db, *, org_id: int, features: dict[str, float]) -> dict[str, Any]:
    """Core prediction over one feature dict (shared by API and agent tool)."""
    values = [features[name] for name in FEATURE_NAMES]

    if not _finite_features(values):
        return {
            "mode": MODE_ERROR,
            "error": "non-finite sensor values; refusing to predict garbage",
            "failure_probability": None,
            "rul_minutes": None,
            "health": None,
            "failure_modes": [],
            "recommendation": "check sensor health",
        }

    try:
        artifact = _load_active_artifact(db, org_id, "failure_classification")
    except Exception as exc:
        logger.warning("PdM artifact unavailable (%s); rules-only fallback", exc)
        artifact = None
        artifact_error = str(exc)
    else:
        artifact_error = None

    if artifact is not None:
        payload, _path = artifact
        try:
            model = payload["model"]
            matrix = np.array([values], dtype=float)
            prob = float(model.predict_proba(matrix)[0][1])
            if not math.isfinite(prob):
                raise ValueError("model produced a non-finite probability")
            modes = _rules_failure_modes(features)  # explainability only
            health = max(0.0, min(1.0, 1.0 - prob))
            rul = _rul_if_available(db, org_id, features)
            return {
                "mode": MODE_MODEL,
                "model_selected": payload.get("selected"),
                "failure_probability": round(prob, 4),
                "rul_minutes": rul,
                "health": round(health, 4),
                "failure_modes": modes,
                "recommendation": _recommendation(health),
            }
        except Exception as exc:
            # Corrupt/changed artifact or model error → ERROR, never rules.
            logger.warning("PdM model prediction failed: %s", exc)
            return {
                "mode": MODE_ERROR,
                "error": f"model prediction failed: {exc}",
                "failure_probability": None,
                "rul_minutes": None,
                "health": None,
                "failure_modes": [],
                "recommendation": "inspect model artifact",
            }

    if artifact_error is not None and "checksum" in artifact_error.lower():
        return {
            "mode": MODE_ERROR,
            "error": artifact_error,
            "failure_probability": None,
            "rul_minutes": None,
            "health": None,
            "failure_modes": [],
            "recommendation": "retrain or re-register the model",
        }

    # Explicit, labeled rules-only fallback.
    modes = _rules_failure_modes(features)
    health = 1.0 if not modes else (0.5 if len(modes) == 1 else 0.0)
    return {
        "mode": MODE_RULES,
        "note": "no registered PdM artifact; documented AI4I mirror thresholds only",
        "failure_probability": None,
        "rul_minutes": None,
        "health": health,
        "failure_modes": modes,
        "recommendation": _recommendation(health),
    }


def _rul_if_available(db, org_id: int, features: dict[str, float]) -> float | None:
    """Attach RUL when an active regressor exists; None never blocks the answer."""
    try:
        artifact = _load_active_artifact(db, org_id, "rul_regression")
        if artifact is None:
            return None
        payload, _path = artifact
        model = payload["model"]
        matrix = np.array([[features[name] for name in FEATURE_NAMES]], dtype=float)
        value = float(model.predict(matrix)[0])
        return round(value, 1) if math.isfinite(value) else None
    except Exception as exc:  # noqa: BLE001 - RUL is best-effort, never fatal
        logger.warning("RUL prediction unavailable: %s", exc)
        return None
