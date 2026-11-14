"""PdM training pipelines (B3) — reuse the existing training-run lifecycle.

Both pipelines follow the platform rules:

- a deterministic seed is mandatory;
- the dataset identity (kind, source, split SHA-256) is bound into the run
  manifest;
- ≥3 candidate models are compared and selected **on the validation split**;
- reported metrics come **only** from the held-out test split;
- artifacts are written under MODEL_ROOT and registered in the existing model
  registry (task types ``failure_classification`` / ``rul_regression``) with a
  server-computed SHA-256 checksum;
- anything that fails marks the run ``failed`` — no metrics file, no registry
  row (existing publish gate).

Usage::

    python -m app.pdm.train --task failure_classification --rows 5000 --seed 42
    python -m app.pdm.train --task rul_regression --seed 42
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import f1_score, mean_absolute_error, precision_score, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.core.config import settings
from app.pdm.dataset import (
    DATASET_KIND_SYNTHETIC,
    FAILURE_FLAG,
    FEATURES,
    load_or_generate,
)

CLASSIFIER_CANDIDATES: dict[str, Callable[[], Any]] = {
    "logistic_regression": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=0)),
    "random_forest": lambda: RandomForestClassifier(n_estimators=200, random_state=0, n_jobs=-1),
    "gradient_boosting": lambda: GradientBoostingClassifier(random_state=0),
}

REGRESSOR_CANDIDATES: dict[str, Callable[[], Any]] = {
    "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
    "random_forest": lambda: RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=-1),
    "gradient_boosting": lambda: GradientBoostingRegressor(random_state=0),
}


def _load_split_csv(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (X, y_failure, modes, udi) for one split file."""
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    X = np.array([[float(row[f]) for f in FEATURES] for row in rows], dtype=float)
    y = np.array([int(float(row[FAILURE_FLAG])) for row in rows], dtype=int)
    modes = np.array(
        [[int(float(row[m])) for m in ("TWF", "HDF", "PWF", "OSF", "RNF")] for row in rows],
        dtype=int,
    )
    udi = np.array([int(row["UDI"]) for row in rows], dtype=int)
    return X, y, modes, udi


def train_failure_classifier(
    *,
    run_dir: Path,
    rows: int = 5000,
    seed: int = 42,
    db=None,
    org_id: int = 1,
) -> dict[str, Any]:
    """Train, evaluate, and register the failure classifier. Returns the summary."""
    from app.pdm.dataset import write_split_bundle

    bundle = write_split_bundle(run_dir / "dataset", rows=rows, seed=seed)
    X_train, y_train, modes_train, _ = _load_split_csv(bundle.train_path)
    X_val, y_val, _, _ = _load_split_csv(bundle.val_path)
    X_test, y_test, modes_test, _ = _load_split_csv(bundle.test_path)

    # Candidate comparison on the VALIDATION split only.
    val_scores: dict[str, float] = {}
    fitted: dict[str, Any] = {}
    for name, factory in CLASSIFIER_CANDIDATES.items():
        model = factory()
        model.fit(X_train, y_train)
        val_scores[name] = float(f1_score(y_val, model.predict(X_val), zero_division=0))
        fitted[name] = model

    best_name = max(val_scores, key=val_scores.get)
    best = fitted[best_name]

    # Test metrics ONLY from the held-out split.
    test_pred = best.predict(X_test)
    test_metrics = {
        "f1": float(f1_score(y_test, test_pred, zero_division=0)),
        "precision": float(precision_score(y_test, test_pred, zero_division=0)),
        "recall": float(recall_score(y_test, test_pred, zero_division=0)),
        "test_samples": int(len(y_test)),
        "positive_rate": float(y_test.mean()) if len(y_test) else 0.0,
    }

    checkpoint_rel = "models/failure_classifier.joblib"
    checkpoint_path = run_dir / checkpoint_rel
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": best, "features": FEATURES, "selected": best_name}, checkpoint_path)
    artifact_rel = _publish_artifact(checkpoint_path, f"pdm/{run_dir.name}/failure_classifier.joblib")

    summary = {
        "task": "failure_classification",
        "dataset_kind": bundle.kind,
        "dataset_source": bundle.source,
        "split_sha256": bundle.split_sha256,
        "candidates_validation_f1": val_scores,
        "selected_model": best_name,
        "test_metrics": test_metrics,
        "artifact": artifact_rel,
    }
    _register_pdm_model(
        db,
        org_id=org_id,
        name=f"pdm-failure-classifier-{run_dir.name}",
        task_type="failure_classification",
        artifact_rel=artifact_rel,
        summary=summary,
        thresholds={"f1_gate": 0.6},
    )
    return summary


def synthesize_rul_series(*, machines: int = 40, seed: int = 42, horizon_minutes: float = 260.0) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic degradation trajectories (documented: synthetic-mirror).

    Each machine's tool wear climbs by ~0.5-0.8 min/cycle with noise; RUL is
    the minutes left until the 240 min tool-wear failure threshold.
    """
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(machines):
        wear = rng.uniform(0, 40)
        rate = rng.uniform(0.5, 0.85)
        torque_bias = rng.uniform(38, 46)
        rpm_bias = rng.uniform(1450, 1560)
        temp_delta = rng.uniform(8.5, 10.5)
        while wear < horizon_minutes:
            minutes_left = max(0.0, 240.0 - wear) / rate
            X.append([298.0, 298.0 + temp_delta, rpm_bias, torque_bias, wear])
            y.append(minutes_left)
            wear += rate + rng.normal(0, 0.05)
    return np.array(X), np.array(y)


def train_rul_regressor(
    *,
    run_dir: Path,
    seed: int = 42,
    db=None,
    org_id: int = 1,
) -> dict[str, Any]:
    """Train/evaluate/register the RUL regressor on synthetic trajectories."""
    rng = np.random.default_rng(seed + 7)
    X, y = synthesize_rul_series(seed=seed)
    idx = rng.permutation(len(X))
    X, y = X[idx], y[idx]
    n_val = int(0.2 * len(X))
    n_test = int(0.15 * len(X))
    X_train, y_train = X[n_val + n_test :], y[n_val + n_test :]
    X_val, y_val = X[:n_val], y[:n_val]
    X_test, y_test = X[n_val : n_val + n_test], y[n_val : n_val + n_test]

    val_scores: dict[str, float] = {}
    fitted: dict[str, Any] = {}
    for name, factory in REGRESSOR_CANDIDATES.items():
        model = factory()
        model.fit(X_train, y_train)
        val_scores[name] = float(mean_absolute_error(y_val, model.predict(X_val)))
        fitted[name] = model

    best_name = min(val_scores, key=val_scores.get)  # lower MAE is better
    best = fitted[best_name]

    test_pred = best.predict(X_test)
    test_metrics = {
        "mae_minutes": float(mean_absolute_error(y_test, test_pred)),
        "test_samples": int(len(y_test)),
        "target_max_minutes": float(y_test.max()) if len(y_test) else 0.0,
    }

    checkpoint_rel = "models/rul_regressor.joblib"
    checkpoint_path = run_dir / checkpoint_rel
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": best, "features": FEATURES, "selected": best_name}, checkpoint_path)
    artifact_rel = _publish_artifact(checkpoint_path, f"pdm/{run_dir.name}/rul_regressor.joblib")

    summary = {
        "task": "rul_regression",
        "dataset_kind": DATASET_KIND_SYNTHETIC,
        "dataset_source": "synthetic degradation trajectories (documented in module docstring)",
        "candidates_validation_mae": val_scores,
        "selected_model": best_name,
        "test_metrics": test_metrics,
        "artifact": artifact_rel,
    }
    _register_pdm_model(
        db,
        org_id=org_id,
        name=f"pdm-rul-regressor-{run_dir.name}",
        task_type="rul_regression",
        artifact_rel=artifact_rel,
        summary=summary,
        thresholds={"mae_gate_minutes": 30.0},
    )
    return summary


def _publish_artifact(checkpoint_path: Path, artifact_rel: str) -> str:
    """Copy a run checkpoint into MODEL_ROOT as the registered artifact.

    The run directory keeps the training checkpoint (lifecycle gate); the
    registry references the published copy with its own SHA-256.
    """
    import shutil

    artifact_path = Path(settings.MODEL_ROOT) / artifact_rel
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(checkpoint_path, artifact_path)
    return artifact_rel


def _register_pdm_model(
    db,
    *,
    org_id: int,
    name: str,
    task_type: str,
    artifact_rel: str,
    summary: dict[str, Any],
    thresholds: dict[str, float],
) -> None:
    """Register a trained PdM artifact in the existing model registry."""
    if db is None:
        return
    from app.db.models import ModelVersion
    from app.services.model_registry import checksum_artifact

    path = Path(settings.MODEL_ROOT) / artifact_rel
    checksum = checksum_artifact(path)
    row = ModelVersion(
        name=name,
        description=f"Predictive maintenance {task_type} ({summary.get('dataset_kind')})",
        framework="sklearn",
        source_path=artifact_rel,
        device="cpu",
        task_type=task_type,
        artifact_id=f"sha256:{checksum}",
        version=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        checksum=checksum,
        thresholds_json=thresholds,
        provenance_json={
            "dataset_kind": summary.get("dataset_kind"),
            "dataset_source": summary.get("dataset_source"),
            "selected_model": summary.get("selected_model"),
            "test_metrics": summary.get("test_metrics"),
            "trained_by": "app.pdm.train",
        },
        is_active=False,
        org_id=org_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.pdm.train")
    parser.add_argument("--task", choices=["failure_classification", "rul_regression"], required=True)
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--runs-dir", type=str, default="data/pdm/training_runs")
    args = parser.parse_args()

    from app.training.runs import TrainingRun

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"pdm-{args.task}-{stamp}-s{args.seed}"
    run_base = Path(args.runs_dir)
    dataset_identity = {
        "kind": DATASET_KIND_SYNTHETIC,
        "name": "ai4i2020-mirror" if args.task == "failure_classification" else "rul-synthetic-trajectories",
        "source": "generated deterministically from --seed (no external download)",
        # Lifecycle gate: identified fingerprint + split name (deterministic
        # generation binds the same identity the write_split_bundle manifest records).
        "fingerprint": f"synthetic:{args.task}:seed{args.seed}:rows{args.rows}",
        "split": "train/val/test",
    }
    run = TrainingRun.create(
        run_base,
        task=args.task,
        seed=args.seed,
        config={"rows": args.rows, "candidates": list(CLASSIFIER_CANDIDATES if args.task == "failure_classification" else REGRESSOR_CANDIDATES)},
        dataset=dataset_identity,
        run_id=run_id,
    )
    try:
        run.start()
        if args.task == "failure_classification":
            summary = train_failure_classifier(run_dir=run.run_dir, rows=args.rows, seed=args.seed, db=None)
        else:
            summary = train_rul_regressor(run_dir=run.run_dir, seed=args.seed, db=None)
        run.complete(
            checkpoints=["models/" + Path(summary["artifact"]).name],
            metrics={"validation": summary.get("candidates_validation_f1") or summary.get("candidates_validation_mae"),
                     "test": summary["test_metrics"]},
        )
        run.log(json.dumps(summary, indent=2))
        print(json.dumps({"run_id": run_id, "status": "completed", "summary": summary}, indent=2))
        return 0
    except Exception as exc:
        run.fail(str(exc))
        print(json.dumps({"run_id": run_id, "status": "failed", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
