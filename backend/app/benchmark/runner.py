"""Benchmark runner (T23): identical input, warmup, gated statistics.

Protocol guarantees, enforced in code:

- **Identical input** — every iteration receives the same array; the input's
  SHA-256 is verified before *and* after the run, so an engine that mutates
  its input fails the benchmark instead of silently breaking comparability.
- **Warmup** — configured warmup iterations run before measurement and are
  excluded from every statistic.
- **Gated statistics** — average, P50, P95, and P99 are reported only when at
  least ``min_samples`` measurements exist; otherwise they are ``null`` with an
  explicit reason. Observed counts, min, and max are always honest raw facts.
- **No fabricated numbers** — an invalid measurement, a failed inference, or a
  protocol violation raises ``BenchmarkError``; no report is assembled at all.

Report numbers are wall-clock milliseconds around the engine's ``predict``
call (``time.perf_counter``), one sample per measured iteration.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from app.services.runtime_metrics import collect_hardware, percentile

BENCHMARK_REPORT_VERSION = 1
DEFAULT_ITERATIONS = 100
DEFAULT_WARMUP = 5
#: Average/P50/P95/P99 require at least this many samples (T23 report rules).
DEFAULT_MIN_SAMPLES = 30


class BenchmarkError(Exception):
    """The benchmark cannot run or complete; no report is produced."""


# ─────────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────────


def sha256_array(array: np.ndarray) -> str:
    """SHA-256 over the array's raw (C-contiguous) bytes."""
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def synthetic_input(seed: int, width: int, height: int, channels: int = 3) -> np.ndarray:
    """Deterministic synthetic image; explicitly labeled synthetic, never industrial."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width, channels), dtype=np.uint8)


def load_input(
    *,
    path: str | Path | None = None,
    seed: int | None = None,
    width: int = 640,
    height: int = 640,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build the benchmark input and its identity metadata.

    Exactly one of ``path`` or ``seed`` must be given. The returned metadata
    records the input source, SHA-256 over decoded pixels, and dimensions —
    these are the values later runs must match to be comparable.
    """
    if path is not None and seed is not None:
        raise BenchmarkError("Provide either an input file or a synthetic seed, not both")
    if path is None and seed is None:
        raise BenchmarkError("An input is required: provide an input file or a synthetic seed")

    if path is not None:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise BenchmarkError(f"Input image could not be decoded: {path}")
        array = np.ascontiguousarray(image)
        meta: dict[str, Any] = {"source": "file", "path": str(path)}
    else:
        if width < 1 or height < 1:
            raise BenchmarkError("width and height must be >= 1")
        array = synthetic_input(int(seed), int(width), int(height))
        meta = {"source": "synthetic", "seed": int(seed)}

    meta.update(
        {
            "sha256": sha256_array(array),
            "width": int(array.shape[1]),
            "height": int(array.shape[0]),
            "channels": int(array.shape[2]) if array.ndim == 3 else 1,
            "dtype": str(array.dtype),
        }
    )
    return array, meta


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────


def summarize(samples: list[float], min_samples: int) -> dict[str, Any]:
    """Gate average/percentiles on having enough samples; never invent values."""
    if min_samples < 1:
        raise BenchmarkError("min_samples must be >= 1")
    ordered = sorted(samples)
    measurements: dict[str, Any] = {
        "unit": "ms",
        "sample_count": len(ordered),
        "samples_ms": list(samples),
        "min_ms": ordered[0] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
        "stats": None,
        "stats_withheld_reason": None,
    }
    if not ordered:
        measurements["stats_withheld_reason"] = "no samples were collected"
    elif len(ordered) < min_samples:
        measurements["stats_withheld_reason"] = (
            f"fewer than {min_samples} samples (collected {len(ordered)}); "
            "average and percentiles are withheld"
        )
    else:
        measurements["stats"] = {
            "average_ms": sum(ordered) / len(ordered),
            "p50_ms": percentile(ordered, 0.50),
            "p95_ms": percentile(ordered, 0.95),
            "p99_ms": percentile(ordered, 0.99),
        }
    return measurements


def _detection_count(result: Any) -> int | None:
    if isinstance(result, dict):
        detections = result.get("detections")
    else:
        detections = getattr(result, "detections", None)
    return len(detections) if isinstance(detections, list) else None


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────


def run_benchmark(
    predict: Callable[[np.ndarray], Any],
    *,
    input_array: np.ndarray,
    input_meta: dict[str, Any],
    iterations: int = DEFAULT_ITERATIONS,
    warmup: int = DEFAULT_WARMUP,
    batch_size: int = 1,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    engine_meta: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run a controlled benchmark and return a completed report dict.

    Raises ``BenchmarkError`` on any failure — callers must not write a report
    when that happens (``write_report`` refuses non-completed reports).
    """
    if iterations < 1:
        raise BenchmarkError("iterations must be >= 1")
    if warmup < 0:
        raise BenchmarkError("warmup must be >= 0")
    if min_samples < 1:
        raise BenchmarkError("min_samples must be >= 1")
    if batch_size != 1:
        raise BenchmarkError(
            f"batch_size={batch_size} is not supported by the current single-frame "
            "inference adapters; refusing to report batched numbers"
        )

    input_sha = sha256_array(input_array)
    declared_sha = input_meta.get("sha256")
    if declared_sha and declared_sha != input_sha:
        raise BenchmarkError("Input metadata checksum does not match the provided input array")

    for index in range(warmup):
        try:
            predict(input_array)
        except Exception as exc:
            raise BenchmarkError(
                f"warmup iteration {index + 1} of {warmup} failed: {exc}"
            ) from exc

    samples: list[float] = []
    last_result: Any = None
    for index in range(iterations):
        started = clock()
        try:
            result = predict(input_array)
        except Exception as exc:
            raise BenchmarkError(
                f"iteration {index + 1} of {iterations} failed: {exc}"
            ) from exc
        elapsed_ms = (clock() - started) * 1000.0
        if not math.isfinite(elapsed_ms) or elapsed_ms < 0:
            raise BenchmarkError(
                f"iteration {index + 1} produced an invalid latency sample "
                f"({elapsed_ms!r} ms); refusing to report it"
            )
        samples.append(elapsed_ms)
        last_result = result

    if sha256_array(input_array) != input_sha:
        raise BenchmarkError("Input array was mutated during benchmarking; identical-input protocol violated")

    measurements = summarize(samples, min_samples)
    return {
        "report_version": BENCHMARK_REPORT_VERSION,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "input": dict(input_meta),
            "iterations": iterations,
            "warmup_iterations": warmup,
            "batch_size": batch_size,
            "min_samples_for_stats": min_samples,
            "clock": "time.perf_counter",
        },
        "engine": dict(engine_meta or {}),
        "environment": dict(environment if environment is not None else collect_hardware()),
        "measurements": measurements,
        "last_inference": (
            {"detection_count": _detection_count(last_result)}
            if _detection_count(last_result) is not None
            else None
        ),
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write a completed report as JSON (temp file + atomic replace).

    Refuses reports that are not completed or whose statistics violate the
    minimum-sample rule, so a fabricated or partial report cannot be persisted
    through this function.
    """
    if report.get("status") != "completed":
        raise BenchmarkError("Refusing to write a benchmark report that is not completed")
    measurements = report.get("measurements") or {}
    protocol = report.get("protocol") or {}
    stats = measurements.get("stats")
    if stats is not None:
        needed = int(protocol.get("min_samples_for_stats", 0))
        if int(measurements.get("sample_count", 0)) < needed:
            raise BenchmarkError(
                "Refusing to write a report whose statistics fall below the minimum sample count"
            )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
