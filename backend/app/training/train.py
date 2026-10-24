"""Reproducible object-detection training entry point (T21).

Usage (from ``backend/``)::

    python -m app.training.train \
        --dataset-root ../datasets/neu_det \
        --report ../datasets/neu_det.validation.json \
        --config train_config.json \
        --runs-dir data/runs

Contract:

- the dataset must have a *passing* validation report; its fingerprint and
  the selected split list become the run's dataset identity;
- the run directory records config snapshot, seed, environment, log, and
  checkpoints under one place;
- ultralytics is an optional runtime: when unavailable the run is marked
  failed and **no metrics file is written**;
- ``metrics.json`` is written only by a completed run (see
  :class:`app.training.runs.TrainingRun`).

Exit codes: 0 completed run, 1 invalid input/failed training, 2 missing
training runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

from app.training.runs import RunError, TrainingRun

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_RUNTIME = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.training.train",
        description="Train an object detector reproducibly on a validated dataset split.",
    )
    parser.add_argument("--dataset-root", required=True, help="Validated dataset root")
    parser.add_argument("--report", required=True, help="Passing dataset validation report (JSON)")
    parser.add_argument("--config", required=True, help="Training config (JSON object)")
    parser.add_argument("--runs-dir", default="data/runs", help="Base directory for run folders")
    parser.add_argument("--split", default="train", help="Dataset split to train on (default: train)")
    parser.add_argument("--seed", type=int, default=None, help="Override config seed")
    return parser


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc


def _dataset_identity(report: dict[str, Any], report_path: Path, dataset_root: Path, split: str) -> dict[str, Any]:
    """Derive the run's dataset identity from a passing validation report."""
    summary = report.get("summary") or {}
    if summary.get("ok") is not True:
        raise ValueError(
            "dataset report does not pass validation "
            f"({summary.get('errors', '?')} error(s)); fix the dataset and re-run validation"
        )
    fingerprint = (report.get("fingerprint") or {}).get("value")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("dataset report has no content fingerprint")

    reported_root = Path(str(report.get("dataset_root") or "")).resolve()
    if reported_root != dataset_root.resolve():
        raise ValueError(
            f"dataset root mismatch: report was generated for {reported_root}, "
            f"got {dataset_root.resolve()}"
        )

    split_files = ((report.get("split") or {}).get("files") or {}).get(split)
    if not isinstance(split_files, list) or not split_files:
        raise ValueError(f"dataset report does not identify a non-empty {split!r} split")

    digest = hashlib.sha256()
    digest.update(f"{split}\n".encode("utf-8"))
    for relative in split_files:
        digest.update(f"{relative}\n".encode("utf-8"))
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    return {
        "fingerprint": fingerprint,
        "split": split,
        "split_items": len(split_files),
        "split_sha256": digest.hexdigest(),
        "report_sha256": report_sha,
    }


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np  # noqa: PLC0415 - optional dependency

        np.random.seed(seed % (2**32))
    except Exception:
        pass
    try:
        import torch  # noqa: PLC0415 - optional dependency

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def _jsonable_metrics(results: Any) -> dict[str, Any]:
    raw = getattr(results, "results_dict", None)
    if not isinstance(raw, dict):
        return {}
    metrics: dict[str, Any] = {}
    for key, value in raw.items():
        try:
            metrics[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return metrics


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    dataset_root = Path(args.dataset_root)
    report_path = Path(args.report)
    config_path = Path(args.config)

    try:
        report = _load_json(report_path, "dataset report")
        config = _load_json(config_path, "training config")
        if not isinstance(report, dict):
            raise ValueError("dataset report must be a JSON object")
        if not isinstance(config, dict):
            raise ValueError("training config must be a JSON object")
        seed = args.seed if args.seed is not None else config.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("training requires an integer seed (config['seed'] or --seed)")
        if not isinstance(config.get("data"), str) or not config["data"].strip():
            raise ValueError("training config requires a 'data' dataset description")
        if not isinstance(config.get("model"), str) or not config["model"].strip():
            raise ValueError("training config requires a 'model' reference")
        identity = _dataset_identity(report, report_path, dataset_root, args.split)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    effective_config = dict(config)
    effective_config["seed"] = seed

    try:
        run = TrainingRun.create(
            args.runs_dir,
            task=str(config.get("task", "object_detection")),
            seed=seed,
            config=effective_config,
            dataset=identity,
        )
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    run.start()
    run.log(f"dataset fingerprint={identity['fingerprint'][:16]}… split={identity['split']}")
    print(f"run {run.manifest['run_id']}: dir={run.run_dir}")

    try:
        from ultralytics import YOLO  # noqa: PLC0415 - optional training runtime
    except Exception as exc:
        reason = f"training runtime unavailable (ultralytics): {exc}"
        run.fail(reason)
        print(f"error: {reason}", file=sys.stderr)
        print("no metrics were recorded", file=sys.stderr)
        return EXIT_NO_RUNTIME

    _seed_everything(seed)
    run.log(f"seeded python/numpy/torch with {seed}")

    train_kwargs: dict[str, Any] = {
        "data": config["data"],
        "imgsz": config.get("imgsz", 640),
        "epochs": config.get("epochs", 10),
        "batch": config.get("batch", 16),
        "device": config.get("device", "cpu"),
        "seed": seed,
        "project": str(run.run_dir),
        "name": "fit",
        "exist_ok": True,
    }
    for key in ("workers", "patience", "optimizer", "lr0"):
        if key in config:
            train_kwargs[key] = config[key]
    train_kwargs.update(config.get("trainer_kwargs") or {})
    train_kwargs["seed"] = seed  # seed is never overridable by trainer_kwargs
    train_kwargs["project"] = str(run.run_dir)
    train_kwargs["exist_ok"] = True

    try:
        run.log(f"training {config['model']} for {train_kwargs['epochs']} epoch(s)")
        trainer = YOLO(config["model"])
        results = trainer.train(**train_kwargs)
    except Exception as exc:
        reason = f"training failed: {type(exc).__name__}: {exc}"
        run.fail(reason)
        print(f"error: {reason}", file=sys.stderr)
        return EXIT_FAILED

    checkpoints = sorted(
        path.relative_to(run.run_dir).as_posix()
        for path in run.run_dir.rglob("*.pt")
        if path.is_file()
    )
    metrics = _jsonable_metrics(results)
    if not checkpoints or not metrics:
        reason = "trainer finished without both a checkpoint and metrics; refusing to report"
        run.fail(reason)
        print(f"error: {reason}", file=sys.stderr)
        return EXIT_FAILED

    try:
        run.complete(checkpoints=checkpoints, metrics=metrics)
    except RunError as exc:
        run.fail(f"could not finalize run: {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"run {run.manifest['run_id']} completed: {len(checkpoints)} checkpoint(s)")
    for key in sorted(metrics):
        print(f"  {key} = {metrics[key]}")
    print(f"metrics -> {run.run_dir / 'metrics.json'}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
