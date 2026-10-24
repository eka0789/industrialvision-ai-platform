"""Safe model artifact references for the registry API (T20).

API input is only ever a *relative reference* under the configured
``MODEL_ROOT``: absolute paths, traversal, unsupported extensions, and
anything resolving outside the model root are rejected before any database
work happens. The server — never the client — computes the artifact checksum.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

from app.core.config import settings
from app.datasets.split import sha256_file
from app.schemas.model import ALLOWED_ARTIFACT_EXTENSIONS


class ArtifactReferenceError(ValueError):
    """Raised when an artifact reference is unsafe or invalid."""


def resolve_artifact_reference(
    reference: str,
    *,
    model_root: str | Path | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve a client-supplied artifact reference strictly under MODEL_ROOT."""
    if not reference or not reference.strip():
        raise ArtifactReferenceError("Artifact reference must not be empty")
    if reference != reference.strip():
        raise ArtifactReferenceError("Artifact reference must not contain surrounding whitespace")

    candidate = Path(reference)
    windows_drive = bool(PureWindowsPath(reference).drive)
    if candidate.is_absolute() or windows_drive or reference.startswith(("/", "\\")):
        raise ArtifactReferenceError("Artifact reference must be relative to MODEL_ROOT")
    if ".." in candidate.parts:
        raise ArtifactReferenceError("Artifact reference must not contain parent-directory segments")
    if candidate.suffix.casefold() not in ALLOWED_ARTIFACT_EXTENSIONS:
        raise ArtifactReferenceError(
            f"Artifact extension must be one of: {', '.join(ALLOWED_ARTIFACT_EXTENSIONS)}"
        )

    root = Path(model_root or settings.MODEL_ROOT).expanduser().resolve()
    joined = root / candidate
    try:
        resolved = joined.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise ArtifactReferenceError(f"Artifact not found under MODEL_ROOT: {reference}") from exc

    if not resolved.is_relative_to(root):
        raise ArtifactReferenceError("Artifact reference resolves outside MODEL_ROOT")
    if must_exist and not resolved.is_file():
        raise ArtifactReferenceError("Artifact reference must point to a file")
    return resolved


def checksum_artifact(path: Path) -> str:
    """Server-computed SHA-256 of an artifact's bytes."""
    return sha256_file(path)
