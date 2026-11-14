"""Optional MLflow experiment tracking (E3).

When ``MLFLOW_TRACKING_URI`` is set and the ``mlflow`` package is installed,
completed training runs are logged there as a MIRROR — the local run
directory (``app/training/runs.py`` manifests) remains the single source of
truth. Dual-write semantics are documented in docs/OPERATIONS.md: MLflow
failures are logged and swallowed (mirroring must never break training).
"""

from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def mlflow_enabled() -> bool:
    return bool(settings.MLFLOW_TRACKING_URI.strip())


def log_training_run(
    *,
    run_id: str,
    task: str,
    seed: int,
    config: dict,
    metrics: dict | None,
    status: str,
    artifact_paths: list[str] | None = None,
) -> bool:
    """Mirror a finished run to MLflow. Returns True when logged.

    Never raises: the local lifecycle is authoritative.
    """
    if not mlflow_enabled():
        return False
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
        with mlflow.start_run(run_name=f"{task}-{run_id}"):
            mlflow.log_params({"task": task, "seed": seed, "run_id": run_id, **_flatten(config)})
            if metrics:
                mlflow.log_metrics(_flatten(metrics))
            mlflow.set_tag("status", status)
            for path in artifact_paths or []:
                mlflow.log_artifact(path)
        return True
    except Exception as exc:  # noqa: BLE001 - mirroring must not break training
        logger.warning("MLflow mirror failed for run %s: %s", run_id, exc)
        return False


def _flatten(payload: dict, prefix: str = "") -> dict[str, float | int | str]:
    flat: dict[str, float | int | str] = {}
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, (int, float, str)) or value is None:
            flat[name] = value if value is not None else ""
        else:
            flat[name] = str(value)[:200]
    return flat
