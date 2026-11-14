"""Read-only experiment browser API (G2): scan run directories for manifests.

No mutation, no artifact bytes — manifest metadata + artifact checksums only.
Any authenticated user may read (the data is organizational); artifact files
themselves stay on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.v1.deps import get_current_user
from app.core.config import settings
from app.db.models import User

router = APIRouter(prefix="/training", tags=["experiments"])


def _run_dirs() -> list[Path]:
    dirs = []
    for raw in settings.TRAINING_RUNS_DIRS.split(","):
        path = Path(raw.strip())
        if path.is_dir():
            dirs.append(path)
    return dirs


def _sha256_of(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _serialize_run(run_dir: Path) -> dict[str, Any] | None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or "run_id" not in manifest:
        return None
    checkpoints = []
    for relative in manifest.get("checkpoints", []) or []:
        path = run_dir / relative
        checkpoints.append(
            {
                "path": relative,
                "exists": path.is_file(),
                "sha256": _sha256_of(path) if path.is_file() else None,
                "size_bytes": path.stat().st_size if path.is_file() else None,
            }
        )
    return {
        "run_id": manifest.get("run_id"),
        "task": manifest.get("task"),
        "seed": manifest.get("seed"),
        "status": manifest.get("status"),
        "config": manifest.get("config"),
        "dataset": manifest.get("dataset"),
        "metrics": manifest.get("metrics"),
        "reason": manifest.get("reason"),
        "created_at": manifest.get("created_at"),
        "completed_at": manifest.get("completed_at"),
        "run_dir": str(run_dir),
        "checkpoints": checkpoints,
    }


@router.get("/runs")
def list_runs(_user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """All discoverable training runs (read-only, newest first)."""
    runs = []
    for base in _run_dirs():
        for run_dir in base.iterdir():
            if run_dir.is_dir():
                serialized = _serialize_run(run_dir)
                if serialized:
                    runs.append(serialized)
    runs.sort(key=lambda run: run.get("created_at") or "", reverse=True)
    return runs


@router.get("/runs/{run_id}")
def get_run(run_id: str, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """One run's manifest + checkpoint checksums (path-safe lookup)."""
    if "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid run id")
    for base in _run_dirs():
        candidate = base / run_id
        if candidate.is_dir():
            serialized = _serialize_run(candidate)
            if serialized:
                return serialized
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
