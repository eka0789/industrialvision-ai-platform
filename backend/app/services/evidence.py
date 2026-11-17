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
        candidate = (self.root / evidence_path).resolve(strict=False)
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

    @staticmethod
    def should_save_evidence(decision: str, sequence_num: int = 1, pass_sample_rate: int = 500) -> bool:
        """Smart Evidence Retention policy for high-speed food packaging lines (120+ ppm).

        - FAIL, REVIEW, ERROR: 100% full-resolution retention for HACCP/BPOM audit.
        - PASS: sampled at 1 in ``pass_sample_rate`` products (item #1, #501, #1001...).
        """
        if decision.upper() != "PASS":
            return True
        rate = max(1, int(pass_sample_rate))
        if rate <= 1:
            return True
        return ((max(1, int(sequence_num)) - 1) % rate) == 0


def prune_expired_evidence(
    db,
    storage: EvidenceStorage | None = None,
    *,
    pass_retention_days: int | None = None,
    audit_retention_days: int | None = None,
) -> dict[str, int]:
    """Enforce tiered evidence retention on disk:
    - PASS photos deleted after ``pass_retention_days`` (default 7 days).
    - FAIL/REVIEW/ERROR audit photos retained for ``audit_retention_days`` (default 730 days / 2 years).
    """
    from datetime import datetime, timedelta, timezone

    from app.db.models import Inspection

    store = storage or EvidenceStorage()
    pass_days = pass_retention_days if pass_retention_days is not None else settings.EVIDENCE_PASS_RETENTION_DAYS
    audit_days = audit_retention_days if audit_retention_days is not None else settings.EVIDENCE_AUDIT_RETENTION_DAYS

    now = datetime.now(timezone.utc)
    pass_cutoff = now - timedelta(days=pass_days)
    audit_cutoff = now - timedelta(days=audit_days)

    pass_rows = (
        db.query(Inspection)
        .filter(
            Inspection.decision == "PASS",
            Inspection.evidence_path.isnot(None),
            Inspection.created_at < pass_cutoff,
        )
        .all()
    )
    audit_rows = (
        db.query(Inspection)
        .filter(
            Inspection.decision.in_(["FAIL", "REVIEW", "ERROR"]),
            Inspection.evidence_path.isnot(None),
            Inspection.created_at < audit_cutoff,
        )
        .all()
    )

    pruned_pass = 0
    for row in pass_rows:
        if row.evidence_path:
            try:
                store.delete(row.evidence_path)
            except ValueError:
                pass
            row.evidence_path = None
            pruned_pass += 1

    pruned_audit = 0
    for row in audit_rows:
        if row.evidence_path:
            try:
                store.delete(row.evidence_path)
            except ValueError:
                pass
            row.evidence_path = None
            pruned_audit += 1

    if pruned_pass or pruned_audit:
        db.commit()
    return {
        "pruned_pass": pruned_pass,
        "pruned_audit": pruned_audit,
        "pass_retention_days": int(pass_days),
        "audit_retention_days": int(audit_days),
    }
