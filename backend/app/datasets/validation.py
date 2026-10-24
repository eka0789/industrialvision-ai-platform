"""Dataset validation with a machine-readable JSON report (T18).

Supported layouts (relative to the dataset root):

- flat:   ``images/*.jpg``  paired with ``labels/*.txt`` (same stem)
- split:  ``images/<split>/*.jpg`` paired with ``labels/<split>/*.txt``

The report never embeds dataset bytes; it contains counts, structured
issues, deterministic split assignments, leakage findings, and optional
SHA-256 checksums so a report file itself can be used as a dataset identity.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Sequence

from app.datasets.labels import (
    IMAGE_EXTENSIONS,
    LABEL_EXTENSION,
    expected_family,
    has_image_signature,
    parse_label_text,
    signature_family,
)
from app.datasets.split import (
    assign_groups,
    group_by_source_identity,
    group_identity,
    sha256_file,
)

REPORT_SCHEMA_VERSION = "1.0"
DEFAULT_ISSUE_LIMIT = 1000
_RATIOS = (0.7, 0.2, 0.1)

_decoder_cache: Any = None
_decoder_cache_resolved = False


def _get_decoder() -> Any:
    """Return the OpenCV module when available, else None (optional decode check)."""
    global _decoder_cache, _decoder_cache_resolved
    if not _decoder_cache_resolved:
        try:
            import cv2  # optional dependency, isolated here
        except Exception:
            _decoder_cache = None
        else:
            _decoder_cache = cv2
        _decoder_cache_resolved = True
    return _decoder_cache


def build_report(
    root: str | Path,
    *,
    num_classes: int | None = None,
    seed: int = 42,
    ratios: Sequence[float] = _RATIOS,
    split_names: Sequence[str] | None = None,
    perform_split: bool = True,
    with_checksums: bool = False,
    decode_check: bool = True,
    issue_limit: int = DEFAULT_ISSUE_LIMIT,
) -> dict[str, Any]:
    """Validate a dataset directory and return the machine-readable report."""
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise ValueError(f"Dataset root does not exist or is not a directory: {root_path}")
    if num_classes is not None and num_classes <= 0:
        raise ValueError("num_classes must be positive")
    if issue_limit <= 0:
        raise ValueError("issue_limit must be positive")

    root_resolved = root_path.resolve()
    images_dir = root_resolved / "images"
    labels_dir = root_resolved / "labels"

    issues: list[dict[str, Any]] = []
    counters = {"errors": 0, "warnings": 0}

    def add_issue(severity: str, code: str, path: str | None, message: str, line: int | None = None) -> None:
        counters["errors" if severity == "error" else "warnings"] += 1
        if len(issues) < issue_limit:
            entry: dict[str, Any] = {"severity": severity, "code": code, "message": message}
            if path is not None:
                entry["path"] = path
            if line is not None:
                entry["line"] = line
            issues.append(entry)

    if not images_dir.is_dir():
        add_issue("error", "missing_images_dir", "images", "Dataset has no images/ directory")
        return _finalize(
            root_resolved,
            layout="flat",
            parameters=_parameters(num_classes, seed, ratios, split_names, perform_split, with_checksums, decode_check, issue_limit),
            issues=issues,
            counters=counters,
            images=[],
            labels=[],
            image_hashes={},
            label_hashes={},
            groups=[],
            split=None,
            conflicts=[],
            checksums=None,
            class_histogram={},
            stats={"boxes": 0, "polygons": 0, "paired": 0, "empty_labels": 0, "orphan_labels": 0},
            truncated=False,
        )

    image_files = sorted(
        path
        for path in images_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
    )
    image_rels = [path.relative_to(images_dir).as_posix() for path in image_files]
    has_subdirs = any("/" in rel for rel in image_rels)
    mixed_layout = has_subdirs and any("/" not in rel for rel in image_rels)
    layout = "split" if has_subdirs else "flat"
    if mixed_layout:
        add_issue(
            "error",
            "inconsistent_layout",
            "images",
            "Images mix top-level files and subdirectories; expected flat or <split>/ layout",
        )
    if not image_files:
        add_issue("error", "no_images", "images", "No image files found under images/")

    label_files = (
        sorted(path for path in labels_dir.rglob(f"*{LABEL_EXTENSION}") if path.is_file())
        if labels_dir.is_dir()
        else []
    )
    if not labels_dir.is_dir():
        add_issue("error", "missing_labels_dir", "labels", "Dataset has no labels/ directory")

    class_histogram: dict[str, int] = {}
    boxes = polygons = paired = empty_labels = orphan_labels = 0
    image_hashes: dict[str, str] = {}
    label_hashes: dict[str, str] = {}
    label_owner: dict[str, list[str]] = {}

    decoder = _get_decoder() if decode_check else None
    if decode_check and decoder is None:
        add_issue(
            "warning",
            "decode_check_skipped",
            None,
            "OpenCV not importable; corrupt-file checks limited to file signature and size",
        )

    for path, rel in zip(image_files, image_rels):
        label_rel = Path(rel).with_suffix(LABEL_EXTENSION).as_posix()
        label_owner.setdefault(label_rel, []).append(rel)
        try:
            image_hashes[rel] = sha256_file(path)
        except OSError as exc:
            add_issue("error", "unreadable_image", f"images/{rel}", f"Image unreadable: {exc}")
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            add_issue("error", "unreadable_image", f"images/{rel}", f"Image unreadable: {exc}")
            continue
        if size == 0:
            add_issue("error", "empty_image", f"images/{rel}", "Image file is empty (0 bytes)")
            continue
        if not has_image_signature(path):
            add_issue(
                "error",
                "corrupt_image",
                f"images/{rel}",
                "Image does not start with a recognized image signature",
            )
            continue
        family = signature_family(path)
        expected = expected_family(path.suffix)
        if family and expected and family != expected:
            add_issue(
                "warning",
                "image_extension_mismatch",
                f"images/{rel}",
                f"Extension {path.suffix} but content looks like {family}",
            )
        if decoder is not None:
            try:
                decoded = decoder.imread(str(path), decoder.IMREAD_UNCHANGED)
            except Exception as exc:  # pragma: no cover - defensive
                decoded = None
                _ = exc
            if decoded is None:
                add_issue(
                    "error",
                    "undecodable_image",
                    f"images/{rel}",
                    "Image signature present but the file cannot be decoded",
                )
                continue

        if not labels_dir.is_dir():
            continue
        label_path = labels_dir / label_rel
        if not label_path.is_file():
            add_issue("error", "missing_label", f"images/{rel}", "No label file for this image")
            continue
        paired += 1
        try:
            label_text = label_path.read_text(encoding="utf-8")
            if with_checksums:
                label_hashes[label_rel] = sha256_file(label_path)
        except UnicodeDecodeError:
            add_issue("error", "unreadable_label", f"labels/{label_rel}", "Label file is not valid UTF-8 text")
            continue
        except OSError as exc:
            add_issue("error", "unreadable_label", f"labels/{label_rel}", f"Label file unreadable: {exc}")
            continue

        if not label_text.strip():
            empty_labels += 1  # a legitimate negative sample in YOLO-style datasets
            continue

        records, label_issues = parse_label_text(label_text, num_classes=num_classes)
        for issue in label_issues:
            add_issue(
                "error",
                issue.code,
                f"labels/{label_rel}",
                issue.message,
                line=issue.line,
            )
        for record in records:
            if record.kind == "box":
                boxes += 1
            else:
                polygons += 1
            key = str(record.class_id)
            class_histogram[key] = class_histogram.get(key, 0) + 1

    for path in label_files:
        rel = path.relative_to(labels_dir).as_posix()
        if rel not in label_owner:
            orphan_labels += 1
            add_issue("error", "orphan_label", f"labels/{rel}", "Label file has no matching image")

    for label_rel, owners in sorted(label_owner.items()):
        if len(owners) > 1:
            add_issue(
                "error",
                "duplicate_image_stem",
                f"labels/{label_rel}",
                "Multiple images map to the same label file: " + ", ".join(sorted(owners)),
            )

    groups = group_by_source_identity(image_rels, image_hashes)
    duplicate_groups = [
        {
            "identity": group_identity(group, image_hashes),
            "files": sorted(group),
            "content_identical": len({image_hashes.get(name, "") for name in group}) == 1,
        }
        for group in groups
        if len(group) > 1
    ]

    conflicts: list[dict[str, Any]] = []
    if layout == "split" and not mixed_layout:
        for group in groups:
            split_set = {name.split("/", 1)[0] for name in group if "/" in name}
            if len(split_set) > 1:
                conflicts.append(
                    {
                        "identity": group_identity(group, image_hashes),
                        "files": sorted(group),
                        "splits": sorted(split_set),
                    }
                )
        for conflict in conflicts:
            add_issue(
                "error",
                "split_leakage",
                None,
                "Source identity spans multiple splits: "
                + conflict["identity"]
                + " -> "
                + ", ".join(conflict["splits"]),
            )

    split_report: dict[str, Any] | None
    if perform_split:
        split_report = _perform_split(
            groups=groups,
            image_rels=image_rels,
            layout=layout,
            seed=seed,
            ratios=ratios,
            split_names=split_names,
            add_issue=add_issue,
        )
    else:
        split_report = {"performed": False, "reason": "split disabled"}

    checksums = None
    if with_checksums:
        checksums = {
            "algorithm": "sha256",
            "files": {
                **{f"images/{name}": digest for name, digest in sorted(image_hashes.items())},
                **{f"labels/{name}": digest for name, digest in sorted(label_hashes.items())},
            },
        }

    truncated = counters["errors"] + counters["warnings"] > len(issues)
    fingerprint = _fingerprint(image_hashes)

    return _finalize(
        root_resolved,
        layout=layout,
        parameters=_parameters(num_classes, seed, ratios, split_names, perform_split, with_checksums, decode_check, issue_limit),
        issues=issues,
        counters=counters,
        images=image_rels,
        labels=[path.relative_to(labels_dir).as_posix() for path in label_files] if labels_dir.is_dir() else [],
        image_hashes=image_hashes,
        label_hashes=label_hashes,
        groups=groups,
        split=split_report,
        conflicts=conflicts,
        checksums=checksums,
        class_histogram=dict(sorted(class_histogram.items(), key=lambda item: int(item[0]))),
        stats={
            "boxes": boxes,
            "polygons": polygons,
            "paired": paired,
            "empty_labels": empty_labels,
            "orphan_labels": orphan_labels,
        },
        truncated=truncated,
        duplicate_groups=duplicate_groups,
        fingerprint=fingerprint,
    )


def report_bytes(report: dict[str, Any]) -> bytes:
    """Canonical JSON serialization of a report (stable key/order layout)."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _parameters(
    num_classes: int | None,
    seed: int,
    ratios: Sequence[float],
    split_names: Sequence[str] | None,
    perform_split: bool,
    with_checksums: bool,
    decode_check: bool,
    issue_limit: int,
) -> dict[str, Any]:
    return {
        "num_classes": num_classes,
        "seed": int(seed),
        "ratios": [float(ratio) for ratio in ratios],
        "split_names": list(split_names) if split_names else None,
        "perform_split": bool(perform_split),
        "checksums": bool(with_checksums),
        "decode_check": bool(decode_check),
        "issue_limit": int(issue_limit),
    }


def _perform_split(
    *,
    groups: list[list[str]],
    image_rels: list[str],
    layout: str,
    seed: int,
    ratios: Sequence[float],
    split_names: Sequence[str] | None,
    add_issue: Any,
) -> dict[str, Any]:
    from app.datasets.split import DEFAULT_SPLIT_NAMES

    if layout == "split":
        counts: dict[str, int] = {}
        files: dict[str, list[str]] = {}
        for rel in image_rels:
            split = rel.split("/", 1)[0] if "/" in rel else "(root)"
            counts[split] = counts.get(split, 0) + 1
            files.setdefault(split, []).append(rel)
        return {
            "performed": False,
            "source": "existing_directories",
            "counts": dict(sorted(counts.items())),
            "files": {key: sorted(value) for key, value in sorted(files.items())},
        }

    names = tuple(split_names or DEFAULT_SPLIT_NAMES[: len(ratios)])
    try:
        assignments = assign_groups(groups, seed=seed, ratios=ratios, split_names=names)
    except ValueError as exc:
        add_issue("error", "invalid_split_config", None, str(exc))
        return {"performed": False, "reason": str(exc)}
    return {
        "performed": True,
        "method": "grouped_sha256",
        "seed": int(seed),
        "ratios": {name: float(ratio) for name, ratio in zip(names, ratios)},
        "counts": {name: len(value) for name, value in assignments.items()},
        "files": assignments,
    }


def _fingerprint(image_hashes: dict[str, str]) -> dict[str, str]:
    """Stable identity of the dataset content (independent of timestamps/seed)."""
    import hashlib

    digest = hashlib.sha256()
    for name in sorted(image_hashes):
        digest.update(f"{name}:{image_hashes[name]}\n".encode("utf-8"))
    return {"algorithm": "sha256", "value": digest.hexdigest()}


def _finalize(
    root_resolved: Path,
    *,
    layout: str,
    parameters: dict[str, Any],
    issues: list[dict[str, Any]],
    counters: dict[str, int],
    images: list[str],
    labels: list[str],
    image_hashes: dict[str, str],
    label_hashes: dict[str, str],
    groups: list[list[str]],
    split: dict[str, Any] | None,
    conflicts: list[dict[str, Any]],
    checksums: dict[str, Any] | None,
    class_histogram: dict[str, int],
    stats: dict[str, int],
    truncated: bool,
    duplicate_groups: list[dict[str, Any]] | None = None,
    fingerprint: dict[str, str] | None = None,
) -> dict[str, Any]:
    errors = counters["errors"]
    warnings = counters["warnings"]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": "industrialvision-dataset-validate",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "dataset_root": str(root_resolved),
        "layout": layout,
        "parameters": parameters,
        "summary": {
            "images": len(images),
            "labels": len(labels),
            "paired_labels": stats["paired"],
            "empty_labels": stats["empty_labels"],
            "orphan_labels": stats["orphan_labels"],
            "boxes": stats["boxes"],
            "polygons": stats["polygons"],
            "errors": errors,
            "warnings": warnings,
            "issues_truncated": truncated,
            "class_histogram": class_histogram,
            "ok": errors == 0,
        },
        "issues": issues,
        "split": split,
        "leakage": {
            "identity_rules": ["content_sha256", "stem_index_suffix"],
            "groups": len(groups),
            "duplicate_groups": duplicate_groups or [],
            "conflicts": conflicts,
            "ok": not conflicts,
        },
        "fingerprint": fingerprint or {"algorithm": "sha256", "value": None},
        "checksums": checksums,
    }
