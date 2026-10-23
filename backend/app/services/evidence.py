"""Safe, server-generated evidence storage rooted in configured data storage."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.config import settings


class EvidenceStorage:
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.EVIDENCE_ROOT).expanduser().resolve()

    def save_image(self, inspection_id: int, content: bytes, extension: str) -> str:
        if inspection_id <= 0:
            raise ValueError("inspection_id must be positive")
        extension = extension.casefold()
        if extension not in self.ALLOWED_EXTENSIONS:
            raise ValueError("Unsupported evidence image extension")
        if not content:
            raise ValueError("Evidence image is empty")

        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.root / str(inspection_id)
        directory.mkdir(parents=True, exist_ok=True)
        resolved_directory = directory.resolve()
        if not resolved_directory.is_relative_to(self.root):
            raise ValueError("Evidence directory resolves outside EVIDENCE_ROOT")

        filename = f"{uuid.uuid4().hex}{extension}"
        destination = resolved_directory / filename
        temporary = resolved_directory / f".{filename}.tmp"
        if not destination.resolve().is_relative_to(self.root):
            raise ValueError("Evidence file resolves outside EVIDENCE_ROOT")

        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return destination.relative_to(self.root).as_posix()

    def resolve(self, evidence_path: str) -> Path:
        candidate = (self.root / evidence_path).resolve(strict=True)
        if not candidate.is_relative_to(self.root) or not candidate.is_file():
            raise ValueError("Evidence path is invalid")
        return candidate

    def delete(self, evidence_path: str) -> bool:
        candidate = (self.root / evidence_path).resolve(strict=False)
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise ValueError("Evidence path is invalid")
        if not candidate.is_file():
            return False
        candidate.unlink()
        try:
            candidate.parent.rmdir()
        except OSError:
            pass
        return True
