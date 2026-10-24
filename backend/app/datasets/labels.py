"""YOLO-style label parsing shared by dataset validation and evaluation tooling.

Formats accepted (normalized coordinates in [0, 1]):

- Detection box:      ``class_id x_center y_center width height [conf]``
- Segmentation mask:  ``class_id x1 y1 x2 y2 ... `` (even coordinate count,
  at least three points)

Parse results are pure data plus structured issues; nothing is written back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
LABEL_EXTENSION = ".txt"

# extension family -> magic signature test on the leading bytes
_SIGNATURE_CHECKS: dict[str, tuple[str, bytes | None]] = {
    ".jpg": ("jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("jpeg", b"\xff\xd8\xff"),
    ".png": ("png", b"\x89PNG\r\n\x1a\n"),
    ".bmp": ("bmp", b"BM"),
    ".tif": ("tiff", None),
    ".tiff": ("tiff", None),
}

_EPS = 1e-6


@dataclass(frozen=True)
class LabelRecord:
    """One annotation line."""

    kind: str  # "box" | "polygon"
    class_id: int
    coords: tuple[float, ...]
    conf: float | None = None


@dataclass(frozen=True)
class LabelIssue:
    """A single problem found in a label file."""

    code: str
    message: str
    line: int | None = None


def has_image_signature(path: Path) -> bool:
    """Return True when the file starts with a recognized image signature."""
    try:
        with path.open("rb") as stream:
            head = stream.read(16)
    except OSError:
        return False
    if not head:
        return False
    for family, signature in _SIGNATURE_CHECKS.values():
        if signature is None:
            continue
        if head.startswith(signature):
            return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    return False


def signature_family(path: Path) -> str | None:
    """Return the detected signature family name for a file, if recognized."""
    try:
        with path.open("rb") as stream:
            head = stream.read(16)
    except OSError:
        return None
    if not head:
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    for family, signature in _SIGNATURE_CHECKS.values():
        if signature is not None and head.startswith(signature):
            return family
    return None


def expected_family(extension: str) -> str | None:
    """Map a file extension to its expected signature family."""
    family = _SIGNATURE_CHECKS.get(extension.casefold())
    if family is None:
        if extension.casefold() in {".webp"}:
            return "webp"
        if extension.casefold() in {".tif", ".tiff"}:
            return "tiff"
        return None
    return family[0]


def parse_label_text(
    text: str, *, num_classes: int | None = None
) -> tuple[list[LabelRecord], list[LabelIssue]]:
    """Parse label file contents into records plus structured issues."""
    records: list[LabelRecord] = []
    issues: list[LabelIssue] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        tokens = line.split()
        if len(tokens) < 2:
            issues.append(
                LabelIssue(
                    code="invalid_label_format",
                    line=line_number,
                    message="Line must contain a class ID and at least two coordinates",
                )
            )
            continue

        try:
            class_value = float(tokens[0])
        except ValueError:
            issues.append(
                LabelIssue(
                    code="invalid_label_format",
                    line=line_number,
                    message=f"Class ID {tokens[0]!r} is not a number",
                )
            )
            continue
        if not class_value.is_integer():
            issues.append(
                LabelIssue(
                    code="invalid_label_format",
                    line=line_number,
                    message=f"Class ID {tokens[0]!r} is not an integer",
                )
            )
            continue
        class_id = int(class_value)

        if class_id < 0 or (num_classes is not None and class_id >= num_classes):
            issues.append(
                LabelIssue(
                    code="class_id_out_of_range",
                    line=line_number,
                    message=(
                        f"Class ID {class_id} outside "
                        f"[0, {num_classes if num_classes is not None else 'unbounded'})"
                    ),
                )
            )
            continue

        try:
            values = [float(token) for token in tokens[1:]]
        except ValueError:
            issues.append(
                LabelIssue(
                    code="invalid_label_format",
                    line=line_number,
                    message="Coordinates must be numbers",
                )
            )
            continue
        if not all(math.isfinite(value) for value in values):
            issues.append(
                LabelIssue(
                    code="invalid_label_format",
                    line=line_number,
                    message="Coordinates must be finite numbers",
                )
            )
            continue

        if len(values) == 4:
            record, issue = _build_box(class_id, values, line_number)
        elif len(values) == 5:
            # class cx cy w h conf (common for prediction exports)
            box_values = values[:4]
            conf = values[4]
            if conf < -_EPS or conf > 1 + _EPS:
                issues.append(
                    LabelIssue(
                        code="invalid_confidence",
                        line=line_number,
                        message=f"Confidence {conf} outside [0, 1]",
                    )
                )
                continue
            record, issue = _build_box(class_id, box_values, line_number, conf=max(conf, 0.0))
        elif len(values) >= 6 and len(values) % 2 == 0:
            record, issue = _build_polygon(class_id, values, line_number)
        else:
            issues.append(
                LabelIssue(
                    code="invalid_label_format",
                    line=line_number,
                    message=(
                        "Expected 4 coordinates (box), 5 (box+conf), or an even "
                        "count of >= 6 polygon coordinates"
                    ),
                )
            )
            continue

        if issue is not None:
            issues.append(issue)
        if record is not None:
            records.append(record)

    return records, issues


def parse_label_file(
    path: Path, *, num_classes: int | None = None
) -> tuple[list[LabelRecord], list[LabelIssue]]:
    """Read and parse a label file. Unreadable text yields a structured issue."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [], [
            LabelIssue(code="unreadable_label", message="Label file is not valid UTF-8 text")
        ]
    except OSError as exc:
        return [], [LabelIssue(code="unreadable_label", message=f"Label file unreadable: {exc}")]
    return parse_label_text(text, num_classes=num_classes)


def _build_box(
    class_id: int, values: list[float], line: int, conf: float | None = None
) -> tuple[LabelRecord | None, LabelIssue | None]:
    cx, cy, width, height = values
    if width <= _EPS or height <= _EPS:
        return None, LabelIssue(
            code="bbox_invalid_size",
            line=line,
            message=f"Bounding box has non-positive size (w={width}, h={height})",
        )
    if width > 1 + _EPS or height > 1 + _EPS:
        return None, LabelIssue(
            code="bbox_out_of_bounds",
            line=line,
            message=f"Bounding box size exceeds image (w={width}, h={height})",
        )
    if not (0 - _EPS <= cx <= 1 + _EPS and 0 - _EPS <= cy <= 1 + _EPS):
        return None, LabelIssue(
            code="bbox_out_of_bounds",
            line=line,
            message=f"Bounding box center outside image (cx={cx}, cy={cy})",
        )
    x1, x2 = cx - width / 2, cx + width / 2
    y1, y2 = cy - height / 2, cy + height / 2
    if x1 < -_EPS or y1 < -_EPS or x2 > 1 + _EPS or y2 > 1 + _EPS:
        return None, LabelIssue(
            code="bbox_out_of_bounds",
            line=line,
            message=(
                f"Bounding box extends outside image "
                f"(x=[{x1:.6f}, {x2:.6f}], y=[{y1:.6f}, {y2:.6f}])"
            ),
        )
    return (
        LabelRecord(
            kind="box",
            class_id=class_id,
            coords=(cx, cy, width, height),
            conf=conf,
        ),
        None,
    )


def _build_polygon(
    class_id: int, values: list[float], line: int
) -> tuple[LabelRecord | None, LabelIssue | None]:
    if any(value < -_EPS or value > 1 + _EPS for value in values):
        return None, LabelIssue(
            code="polygon_out_of_bounds",
            line=line,
            message="Mask polygon coordinates must lie inside [0, 1]",
        )
    points = list(zip(values[::2], values[1::2]))
    if len(set(points)) < 3:
        return None, LabelIssue(
            code="polygon_degenerate",
            line=line,
            message="Mask polygon needs at least three distinct points",
        )
    area = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        area += x1 * y2 - x2 * y1
    if abs(area) / 2 <= _EPS:
        return None, LabelIssue(
            code="polygon_degenerate",
            line=line,
            message="Mask polygon has zero area",
        )
    return LabelRecord(kind="polygon", class_id=class_id, coords=tuple(values)), None
