"""Source-identity grouping and deterministic dataset splitting (T18).

Two files belong to the same source identity when their bytes are identical
(content SHA-256) or when their filenames share a source stem after the
trailing index suffix is removed (``img_0001.png``/``img_0002.png`` -> stem
``img``). Split assignment hashes ``seed:canonical_file`` so a group is always
assigned as one unit: identical sources can never leak across splits, and the
result is stable across runs, machines, and filesystem ordering.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_SPLIT_NAMES = ("train", "val", "test")
_STEM_INDEX_SUFFIX = re.compile(r"[_\-.]\d{1,}$")


def source_stem(relative_path: str | Path) -> str:
    """Strip a trailing numeric index suffix from a filename stem."""
    stem = Path(relative_path).stem
    stripped = _STEM_INDEX_SUFFIX.sub("", stem)
    return stripped if stripped else stem


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def group_by_source_identity(
    files: Sequence[str],
    content_hashes: dict[str, str],
) -> list[list[str]]:
    """Union files that share content or a source stem.

    Args:
        files: relative image paths (order does not matter).
        content_hashes: mapping of relative path -> SHA-256 hex digest.

    Returns:
        Groups of relative paths; every group with more than one file is a
        potential leakage source if split independently.
    """
    parent: dict[str, str] = {key: key for key in files}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    by_hash: dict[str, str] = {}
    by_stem: dict[str, str] = {}
    for relative in sorted(files):
        digest = content_hashes.get(relative)
        if digest:
            if digest in by_hash:
                union(relative, by_hash[digest])
            else:
                by_hash[digest] = relative
        stem = source_stem(relative)
        if stem in by_stem:
            union(relative, by_stem[stem])
        else:
            by_stem[stem] = relative

    grouped: dict[str, list[str]] = {}
    for relative in sorted(files):
        grouped.setdefault(find(relative), []).append(relative)
    return [grouped[key] for key in sorted(grouped)]


def group_identity(group: Sequence[str], content_hashes: dict[str, str]) -> str:
    """Human-readable identity label for a group, stable across runs."""
    stems = {source_stem(relative) for relative in group}
    if len(stems) == 1:
        return f"stem:{next(iter(stems))}"
    hashes = sorted(content_hashes.get(relative, "") for relative in group)
    hashes = [digest for digest in hashes if digest]
    if hashes:
        return f"content:{hashes[0][:12]}"
    return f"files:{min(group)}"


def assign_groups(
    groups: Iterable[Sequence[str]],
    *,
    seed: int,
    ratios: Sequence[float],
    split_names: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    """Deterministically assign whole groups to named splits.

    The bucket for a group is derived from ``sha256(seed:canonical_file)`` of
    the group's smallest relative path, so results are identical regardless of
    input ordering and a group never straddles two splits.
    """
    names = tuple(split_names or DEFAULT_SPLIT_NAMES[: len(ratios)])
    if len(names) != len(ratios):
        raise ValueError("split_names must match ratios in length")
    _validate_ratios(ratios)

    cumulative: list[float] = []
    total = 0.0
    for ratio in ratios:
        total += ratio
        cumulative.append(total)

    assignments: dict[str, list[str]] = {name: [] for name in names}
    for group in groups:
        files = sorted(group)
        if not files:
            continue
        key = f"{seed}:{files[0]}"
        bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16) / 2**64
        for index, threshold in enumerate(cumulative):
            if bucket < threshold or index == len(cumulative) - 1:
                assignments[names[index]].extend(files)
                break
    for name in names:
        assignments[name].sort()
    return assignments


def _validate_ratios(ratios: Sequence[float]) -> None:
    if len(ratios) not in (2, 3):
        raise ValueError("Exactly two or three split ratios are required")
    if any(ratio <= 0 for ratio in ratios):
        raise ValueError("Split ratios must be positive")
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError("Split ratios must sum to 1.0")
