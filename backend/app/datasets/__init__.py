"""Dataset validation, deterministic splitting, and leakage detection (T18).

The tooling inspects a YOLO-style dataset layout, validates image/label
pairing, class IDs, bounding boxes/masks, empty or corrupt files, performs a
deterministic grouped split, and detects duplicate leakage by source
identity. It emits a machine-readable JSON report; no dataset bytes are
copied, modified, or redistributed.
"""

from app.datasets.split import group_by_source_identity, assign_groups
from app.datasets.validation import build_report, report_bytes

__all__ = ["build_report", "report_bytes", "group_by_source_identity", "assign_groups"]
