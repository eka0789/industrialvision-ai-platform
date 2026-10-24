"""Reproducible training-run manifests with metric reporting gates (T21).

A run directory always contains:

- ``manifest.json`` — task, seed, config snapshot, environment, dataset
  identity (validation-report fingerprint + identified split), status
  timeline, checkpoints, and metrics (only after completion);
- ``run.log`` — timestamped log lines;
- ``metrics.json`` — written only by a *completed* run.

Metrics can only be read through :meth:`TrainingRun.publish_metrics`, which
refuses any run that is not ``completed`` on an identified dataset split.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import secrets
import sys
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "1.0"
MANIFEST_NAME = "manifest.json"
LOG_NAME = "run.log"
METRICS_NAME = "metrics.json"

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"running", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


class RunError(RuntimeError):
    """Raised for invalid run lifecycle operations or missing identity."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def _environment_snapshot() -> dict[str, str]:
    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    try:
        import numpy  # noqa: PLC0415 - optional dependency

        environment["numpy"] = numpy.__version__
    except Exception:
        pass
    try:
        import torch  # noqa: PLC0415 - optional dependency

        environment["torch"] = torch.__version__
    except Exception:
        pass
    try:
        import ultralytics  # noqa: PLC0415 - optional dependency

        environment["ultralytics"] = ultralytics.__version__
    except Exception:
        pass
    return environment


def _validate_dataset_identity(dataset: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(dataset, dict):
        raise RunError("dataset identity must be an object")
    fingerprint = dataset.get("fingerprint")
    split = dataset.get("split")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise RunError("dataset identity requires a non-empty report fingerprint")
    if not isinstance(split, str) or not split.strip():
        raise RunError("dataset identity requires an identified split name")
    return dict(dataset)


class TrainingRun:
    """Lifecycle wrapper around a run directory and its manifest."""

    def __init__(self, run_dir: Path, manifest: dict[str, Any]) -> None:
        self.run_dir = run_dir
        self.manifest = manifest

    # ---------------------------------------------------------------- create
    @classmethod
    def create(
        cls,
        base_dir: str | Path,
        *,
        task: str,
        seed: int,
        config: dict[str, Any],
        dataset: dict[str, Any],
        run_id: str | None = None,
    ) -> TrainingRun:
        if not task or not isinstance(task, str):
            raise RunError("run task must be a non-empty string")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise RunError("run seed must be an integer")
        if not isinstance(config, dict):
            raise RunError("run config must be an object")
        try:
            json.dumps(config, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise RunError("run config must be JSON-serializable") from exc

        identity = _validate_dataset_identity(dataset)
        run_id = run_id or new_run_id()
        if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
            raise RunError("run_id must be a plain directory name")

        run_dir = Path(base_dir) / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunError(f"run directory already exists: {run_dir}") from exc

        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "run_id": run_id,
            "task": task,
            "seed": seed,
            "config": config,
            "dataset": identity,
            "environment": _environment_snapshot(),
            "status": "created",
            "checkpoints": [],
            "metrics": None,
            "reason": None,
            "created_at": _utc_now(),
            "started_at": None,
            "completed_at": None,
            "log": LOG_NAME,
        }
        run = cls(run_dir, manifest)
        run._write_manifest()
        return run

    @classmethod
    def load(cls, run_dir: str | Path) -> TrainingRun:
        run_dir = Path(run_dir)
        manifest_path = run_dir / MANIFEST_NAME
        if not manifest_path.is_file():
            raise RunError(f"no manifest found in {run_dir}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunError(f"manifest unreadable: {exc}") from exc
        if not isinstance(manifest, dict) or "run_id" not in manifest or "status" not in manifest:
            raise RunError("manifest is missing required keys")
        return cls(run_dir, manifest)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._transition("running")
        self.manifest["started_at"] = _utc_now()
        self._write_manifest()
        self.log("run started")

    def complete(self, *, checkpoints: list[str], metrics: dict[str, Any]) -> None:
        """Finish a run; only place where metrics are persisted."""
        if self.manifest["status"] != "running":
            raise RunError(
                f"cannot complete a run in status {self.manifest['status']!r}; must be 'running'"
            )
        if not checkpoints:
            raise RunError("a completed run must reference at least one checkpoint")
        resolved: list[str] = []
        for checkpoint in checkpoints:
            resolved.append(self._resolve_run_file(checkpoint))
        if not isinstance(metrics, dict) or not metrics:
            raise RunError("a completed run must record a non-empty metrics object")
        try:
            json.dumps(metrics, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise RunError("metrics must be JSON-serializable") from exc
        _validate_dataset_identity(self.manifest.get("dataset") or {})

        self._transition("completed")
        self.manifest["completed_at"] = _utc_now()
        self.manifest["checkpoints"] = resolved
        self.manifest["metrics"] = metrics
        self._write_manifest()

        payload = {
            "run_id": self.manifest["run_id"],
            "task": self.manifest["task"],
            "seed": self.manifest["seed"],
            "dataset": self.manifest["dataset"],
            "checkpoints": resolved,
            "metrics": metrics,
            "completed_at": self.manifest["completed_at"],
        }
        self._write_json(self.run_dir / METRICS_NAME, payload)
        self.log("run completed")

    def fail(self, reason: str) -> None:
        if not reason or not isinstance(reason, str):
            raise RunError("failure reason must be a non-empty string")
        if self.manifest["status"] not in ("created", "running"):
            raise RunError(f"cannot fail a run in status {self.manifest['status']!r}")
        self._transition("failed")
        self.manifest["reason"] = reason
        self._write_manifest()
        self.log(f"run failed: {reason}")

    def publish_metrics(self) -> dict[str, Any]:
        """The only sanctioned metrics read: completed runs with identity."""
        status = self.manifest["status"]
        if status != "completed":
            raise RunError(
                f"refusing to report metrics from run {self.manifest['run_id']!r}: "
                f"status is {status!r}, not 'completed'"
            )
        _validate_dataset_identity(self.manifest.get("dataset") or {})
        metrics = self.manifest.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise RunError("completed run has no recorded metrics")
        return {
            "run_id": self.manifest["run_id"],
            "task": self.manifest["task"],
            "seed": self.manifest["seed"],
            "dataset": self.manifest["dataset"],
            "checkpoints": self.manifest["checkpoints"],
            "metrics": metrics,
            "completed_at": self.manifest["completed_at"],
        }

    def log(self, message: str) -> None:
        line = f"[{_utc_now()}] {message}\n"
        with (self.run_dir / LOG_NAME).open("a", encoding="utf-8") as stream:
            stream.write(line)

    # -------------------------------------------------------------- helpers
    def _transition(self, new_status: str) -> None:
        current = self.manifest["status"]
        if new_status not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise RunError(f"invalid run status transition {current!r} -> {new_status!r}")
        self.manifest["status"] = new_status

    def _resolve_run_file(self, relative: str) -> str:
        """Checkpoints must be existing files inside the run directory."""
        if not relative or not isinstance(relative, str):
            raise RunError("checkpoint reference must be a non-empty string")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RunError(f"checkpoint must be a path inside the run directory: {relative}")
        resolved = (self.run_dir / candidate).resolve()
        if not resolved.is_relative_to(self.run_dir.resolve()):
            raise RunError(f"checkpoint resolves outside the run directory: {relative}")
        if not resolved.is_file():
            raise RunError(f"checkpoint not found: {relative}")
        return (self.run_dir / candidate).relative_to(self.run_dir).as_posix()

    def _write_manifest(self) -> None:
        self._write_json(self.run_dir / MANIFEST_NAME, self.manifest)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
