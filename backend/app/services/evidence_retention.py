"""Smart Evidence Retention Policy for Food Safety & HACCP Traceability (Fase 2).

Implements:
1. Ingest-time sampling for high-speed food lines:
   - FAIL & REVIEW: 100% full-resolution image storage with anti-delete audit protection.
   - PASS: Sampling mode (stores 1 out of every N images, default N=100; remaining N-1
     PASS items store full decision metadata in the database without writing image bytes).
2. Anti-delete protection for FAIL/REVIEW evidence during the audit window (default 365+ days).
3. Background RQ worker cleanup task (`cleanup_expired_pass_evidence`) that removes expired
   PASS evidence files while never touching protected FAIL/REVIEW audit evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Inspection
from app.services.evidence import EvidenceStorage

DEFAULT_PASS_SAMPLE_N = 100
DEFAULT_PASS_RETENTION_DAYS = 7
DEFAULT_AUDIT_RETENTION_DAYS = 365


@dataclass(frozen=True)
class RetentionPolicy:
    pass_sample_rate: int = DEFAULT_PASS_SAMPLE_N
    pass_retention_days: int = DEFAULT_PASS_RETENTION_DAYS
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS

    @classmethod
    def from_settings(cls, pass_sample_rate: int | None = None) -> "RetentionPolicy":
        return cls(
            pass_sample_rate=max(1, int(pass_sample_rate or getattr(settings, "EVIDENCE_PASS_SAMPLE_RATE", DEFAULT_PASS_SAMPLE_N))),
            pass_retention_days=max(1, int(getattr(settings, "EVIDENCE_PASS_RETENTION_DAYS", DEFAULT_PASS_RETENTION_DAYS))),
            audit_retention_days=max(30, int(getattr(settings, "EVIDENCE_AUDIT_RETENTION_DAYS", DEFAULT_AUDIT_RETENTION_DAYS))),
        )


def should_retain_image(
    decision: str,
    sequence_index: int = 1,
    pass_sample_rate: int = DEFAULT_PASS_SAMPLE_N,
) -> bool:
    """Return True if the image file should be written to disk under the Smart Retention Policy."""
    norm = (decision or "ERROR").upper()
    if norm in ("FAIL", "REVIEW", "ERROR"):
        return True
    rate = max(1, int(pass_sample_rate))
    if rate <= 1:
        return True
    return ((max(1, int(sequence_index)) - 1) % rate) == 0


def is_audit_protected(
    inspection: Inspection,
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
    now: datetime | None = None,
) -> bool:
    """Return True if an inspection's evidence is protected from deletion under HACCP/BPOM audit rules."""
    if (inspection.decision or "").upper() not in ("FAIL", "REVIEW"):
        return False
    ref_now = now or datetime.now(timezone.utc)
    created = inspection.created_at or ref_now
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    cutoff = ref_now - timedelta(days=max(1, int(audit_retention_days)))
    return created >= cutoff


def delete_evidence_with_audit_guard(
    inspection: Inspection,
    storage: EvidenceStorage | None = None,
    *,
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
    now: datetime | None = None,
) -> bool:
    """Delete an inspection's evidence file ONLY if it is not protected by the HACCP audit lock."""
    if is_audit_protected(inspection, audit_retention_days=audit_retention_days, now=now):
        raise PermissionError(
            f"Inspection #{inspection.id} ({inspection.decision}) evidence is audit-protected for {audit_retention_days} days and cannot be deleted"
        )
    if not inspection.evidence_path:
        return False
    store = storage or EvidenceStorage()
    deleted = store.delete(inspection.evidence_path)
    inspection.evidence_path = None
    return deleted


def persist_inspection_evidence(
    storage: EvidenceStorage,
    *,
    inspection_id: int,
    decision: str,
    sequence_index: int,
    content: bytes,
    extension: str = ".jpg",
    pass_sample_rate: int = DEFAULT_PASS_SAMPLE_N,
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
) -> tuple[str | None, dict[str, Any]]:
    """Apply Smart Evidence Retention at ingest time and return (evidence_path, retention_meta)."""
    retain = should_retain_image(decision, sequence_index=sequence_index, pass_sample_rate=pass_sample_rate)
    norm_decision = (decision or "ERROR").upper()
    protected = norm_decision in ("FAIL", "REVIEW")

    evidence_path: str | None = None
    if retain and content:
        evidence_path = storage.save_image(inspection_id, content, extension)

    return evidence_path, {
        "evidence_sampled": bool(evidence_path is not None),
        "retention_mode": "audit_100pct" if protected else f"pass_sampled_1_in_{max(1, int(pass_sample_rate))}",
        "pass_sample_rate": max(1, int(pass_sample_rate)),
        "sequence_index": int(sequence_index),
        "audit_protected": protected,
        "audit_retention_days": int(audit_retention_days) if protected else None,
    }


def cleanup_expired_pass_evidence(
    db: Session,
    storage: EvidenceStorage | None = None,
    *,
    pass_retention_days: int | None = None,
    audit_retention_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Prune expired PASS evidence files while strictly preserving protected FAIL/REVIEW audit evidence."""
    store = storage or EvidenceStorage()
    pass_days = int(pass_retention_days if pass_retention_days is not None else settings.EVIDENCE_PASS_RETENTION_DAYS)
    audit_days = int(audit_retention_days if audit_retention_days is not None else settings.EVIDENCE_AUDIT_RETENTION_DAYS)
    ref_now = now or datetime.now(timezone.utc)
    pass_cutoff = ref_now - timedelta(days=pass_days)

    expired_pass = (
        db.query(Inspection)
        .filter(
            Inspection.decision == "PASS",
            Inspection.evidence_path.isnot(None),
            Inspection.created_at < pass_cutoff,
        )
        .all()
    )

    pruned_pass = 0
    for row in expired_pass:
        if row.evidence_path:
            try:
                store.delete(row.evidence_path)
            except ValueError:
                pass
            row.evidence_path = None
            pruned_pass += 1

    # Count protected FAIL/REVIEW rows to report honestly that zero protected files were touched
    protected_fail_count = (
        db.query(Inspection)
        .filter(
            Inspection.decision.in_(["FAIL", "REVIEW"]),
            Inspection.evidence_path.isnot(None),
        )
        .count()
    )

    if pruned_pass > 0:
        db.commit()

    return {
        "pruned_pass": pruned_pass,
        "pruned_audit": 0,
        "protected_fail_preserved": int(protected_fail_count),
        "pass_retention_days": pass_days,
        "audit_retention_days": audit_days,
    }
