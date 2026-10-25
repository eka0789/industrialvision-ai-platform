"""Runtime metrics collection (T22).

Measures latency samples, FPS, CPU/RAM, optional GPU, frame drops, reconnects,
and inference errors. Every numeric metric declares its unit in ``UNITS``
(dotted paths that match the snapshot layout; tests assert full coverage),
samples are bounded by explicit measurement windows, and each snapshot carries
hardware metadata so numbers can be interpreted later.

Resource sampling uses ``psutil`` when it is installed and otherwise falls back
to Linux ``/proc``; the active backend is always named in the snapshot so a
missing library degrades to ``"unavailable"`` instead of inventing numbers.
GPU sampling is optional (pynvml, then torch.cuda) and reports
``available: false`` when no runtime is present.

All recording methods are thread-safe: capture workers run in background
threads while snapshots are taken from request handlers.
"""

from __future__ import annotations

import math
import os
import platform
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LATENCY_STAGES: tuple[str, ...] = ("capture", "inference")
LATENCY_FIELDS: tuple[str, ...] = ("count", "mean", "p50", "p95", "p99", "min", "max", "last")

#: Percentiles are reported only once enough samples back them up (T23 rule:
#: never print P50/P95/P99 without enough samples).
MIN_PERCENTILE_SAMPLES = 10

#: Recorded error messages are truncated to keep snapshots bounded.
ERROR_MESSAGE_LIMIT = 300

#: Hard cap on stored frame timestamps regardless of window length.
FRAME_TIMES_HARD_CAP = 100_000

RESOURCE_BACKENDS = ("auto", "psutil", "procfs", "unavailable")

#: Unit registry for every numeric (and null-placeholder) metric in a snapshot.
UNITS: dict[str, str] = {
    "window.elapsed_sec": "seconds",
    "window.latency_window_samples": "samples",
    "window.fps_window_seconds": "seconds",
    "fps.value": "frames/second",
    "fps.frames_in_window": "frames",
    "fps.window_seconds": "seconds",
    "fps.effective_window_sec": "seconds",
    "fps.elapsed_sec": "seconds",
    "counters.frames_processed": "frames",
    "counters.frames_dropped": "frames",
    "counters.reconnects": "count",
    "counters.inference_completed": "inferences",
    "counters.inference_errors": "count",
    "rates.frame_drop_rate": "ratio",
    "rates.inference_error_rate": "ratio",
    "resources.cpu_percent": "percent",
    "resources.memory.used_bytes": "bytes",
    "resources.memory.total_bytes": "bytes",
    "resources.memory.process_rss_bytes": "bytes",
    "resources.memory.percent": "percent",
    "gpu.total_memory_bytes": "bytes",
    "gpu.used_memory_bytes": "bytes",
    "gpu.utilization_percent": "percent",
    "hardware.cpu_count": "cores",
    "hardware.cpu_cores_physical": "cores",
    "hardware.total_ram_bytes": "bytes",
}
for _stage in LATENCY_STAGES:
    UNITS[f"latency.{_stage}.count"] = "samples"
    for _field in ("mean", "p50", "p95", "p99", "min", "max", "last"):
        UNITS[f"latency.{_stage}.{_field}"] = "ms"


# ─────────────────────────────────────────────────────────────────────────────
# Optional dependency probes (patchable in tests)
# ─────────────────────────────────────────────────────────────────────────────


def _import_psutil() -> Any | None:
    """Return the psutil module when installed, else None."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _procfs_available() -> bool:
    return Path("/proc/stat").is_file() and Path("/proc/meminfo").is_file()


# ─────────────────────────────────────────────────────────────────────────────
# /proc readers (Linux fallback for psutil)
# ─────────────────────────────────────────────────────────────────────────────


def _read_proc_cpu_stat() -> tuple[int, int] | None:
    """Aggregate CPU jiffies from /proc/stat as (total, idle+iowait)."""
    try:
        with open("/proc/stat", "r", encoding="ascii") as handle:
            line = handle.readline()
    except OSError:
        return None
    parts = line.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        values = [int(value) for value in parts[1:9]]
    except ValueError:
        return None
    if len(values) < 5:
        return None
    idle = values[3] + values[4]
    return sum(values), idle


def _read_proc_meminfo() -> dict[str, int]:
    """Meminfo values in bytes."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="ascii", errors="replace")
    except OSError:
        return {}
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        parts = rest.split()
        if not parts:
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1].lower() == "kb":
            amount *= 1024
        values[key.strip()] = amount
    return values


def _read_proc_self_rss(page_size: int) -> int | None:
    try:
        parts = Path("/proc/self/statm").read_text(encoding="ascii").split()
    except OSError:
        return None
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]) * page_size
    except ValueError:
        return None


def _read_proc_cpu_model() -> str | None:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "model name":
            return value.strip() or None
    return None


def _read_proc_physical_cores() -> int | None:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cores: set[tuple[str, str]] = set()
    physical: str | None = None
    core: str | None = None
    for line in text.splitlines():
        if not line.strip():
            if physical is not None and core is not None:
                cores.add((physical, core))
            physical = None
            core = None
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key == "physical id":
            physical = value.strip()
        elif key == "core id":
            core = value.strip()
    if physical is not None and core is not None:
        cores.add((physical, core))
    if cores:
        return len(cores)
    info: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() not in info:
            info[key.strip()] = value.strip()
    try:
        return int(info.get("cpu cores", ""))
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GPU (optional)
# ─────────────────────────────────────────────────────────────────────────────


def gpu_snapshot() -> dict[str, Any]:
    """Sample the GPU through pynvml or torch.cuda; never raise."""
    empty: dict[str, Any] = {
        "available": False,
        "source": None,
        "vendor": None,
        "name": None,
        "total_memory_bytes": None,
        "used_memory_bytes": None,
        "utilization_percent": None,
    }
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return {
                "available": True,
                "source": "pynvml",
                "vendor": "NVIDIA",
                "name": str(name),
                "total_memory_bytes": int(memory.total),
                "used_memory_bytes": int(memory.used),
                "utilization_percent": float(utilization.gpu),
            }
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            return {
                "available": True,
                "source": "torch.cuda",
                "vendor": "NVIDIA",
                "name": str(torch.cuda.get_device_name(0)),
                "total_memory_bytes": int(torch.cuda.get_device_properties(0).total_memory),
                "used_memory_bytes": int(torch.cuda.memory_allocated(0)),
                "utilization_percent": None,
            }
    except Exception:
        pass
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# Hardware metadata and helpers
# ─────────────────────────────────────────────────────────────────────────────


def _collect_hardware() -> dict[str, Any]:
    """Static hardware/ runtime metadata, preferring psutil then /proc."""
    physical_cores: int | None = None
    total_ram: int | None = None
    psutil = _import_psutil()
    if psutil is not None:
        try:
            physical_cores = psutil.cpu_count(logical=False)
            total_ram = int(psutil.virtual_memory().total)
        except Exception:
            physical_cores = None
            total_ram = None
    if total_ram is None:
        total_ram = _read_proc_meminfo().get("MemTotal")
    if physical_cores is None:
        physical_cores = _read_proc_physical_cores()
    cpu_model = _read_proc_cpu_model() or platform.processor() or None
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count(),
        "cpu_cores_physical": physical_cores,
        "total_ram_bytes": total_ram,
    }


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _percentile(sorted_samples: list[float], quantile: float) -> float | None:
    """Linear-interpolated percentile over an ascending sample list."""
    if not sorted_samples:
        return None
    if len(sorted_samples) == 1:
        return float(sorted_samples[0])
    position = quantile * (len(sorted_samples) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_samples[lower])
    fraction = position - lower
    low_value = sorted_samples[lower]
    high_value = sorted_samples[upper]
    return float(low_value + (high_value - low_value) * fraction)


def _latency_stats(samples: list[float]) -> dict[str, float | int | None]:
    """Summarize a latency window; percentiles need MIN_PERCENTILE_SAMPLES."""
    if not samples:
        return {field: (0 if field == "count" else None) for field in LATENCY_FIELDS}
    ordered = sorted(samples)
    stats: dict[str, float | int | None] = {
        "count": len(ordered),
        "mean": sum(ordered) / len(ordered),
        "min": float(ordered[0]),
        "max": float(ordered[-1]),
        "last": float(samples[-1]),
    }
    if len(ordered) >= MIN_PERCENTILE_SAMPLES:
        stats["p50"] = _percentile(ordered, 0.50)
        stats["p95"] = _percentile(ordered, 0.95)
        stats["p99"] = _percentile(ordered, 0.99)
    else:
        stats["p50"] = None
        stats["p95"] = None
        stats["p99"] = None
    return {field: stats[field] for field in LATENCY_FIELDS}


# ─────────────────────────────────────────────────────────────────────────────
# Resource sampling
# ─────────────────────────────────────────────────────────────────────────────


class ResourceSampler:
    """CPU/RAM sampling through psutil, /proc, or an explicit opt-out.

    The sampler is not internally synchronized; the owning collector calls it
    while holding its lock.
    """

    def __init__(self, backend: str = "auto", *, clock: Callable[[], float] = time.monotonic):
        if backend not in RESOURCE_BACKENDS:
            raise ValueError(f"Unknown resource backend {backend!r}; expected one of {RESOURCE_BACKENDS}")
        self._clock = clock
        self._psutil: Any | None = None
        self._process: Any | None = None
        self._cpu_prev: tuple[int, int] | None = None
        self._cpu_prev_at: float | None = None
        try:
            self._page_size = os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError, AttributeError):
            self._page_size = 4096

        resolved = backend
        if backend in ("auto", "psutil"):
            self._psutil = _import_psutil()
            if self._psutil is not None:
                resolved = "psutil"
            elif backend == "psutil":
                raise ValueError("psutil backend requested but psutil is not installed")
        if resolved in ("auto", "procfs"):
            if _procfs_available():
                resolved = "procfs"
            elif backend == "procfs":
                raise ValueError("procfs backend requested but /proc is not available on this system")
            else:
                resolved = "unavailable"
        self.backend = resolved
        self.reset()

    def reset(self) -> None:
        """Re-prime CPU delta sampling."""
        if self.backend == "psutil":
            try:
                self._psutil.cpu_percent(interval=None)
                self._process = self._psutil.Process()
            except Exception:
                pass
        elif self.backend == "procfs":
            self._cpu_prev = _read_proc_cpu_stat()
            self._cpu_prev_at = self._clock()

    def cpu_percent(self) -> float | None:
        """System-wide CPU utilization since the previous sample (0–100)."""
        if self.backend == "psutil":
            try:
                return float(self._psutil.cpu_percent(interval=None))
            except Exception:
                return None
        if self.backend == "procfs":
            current = _read_proc_cpu_stat()
            now = self._clock()
            previous, previous_at = self._cpu_prev, self._cpu_prev_at
            self._cpu_prev = current
            self._cpu_prev_at = now
            if current is None or previous is None or now is None or previous_at is None:
                return None
            total_delta = current[0] - previous[0]
            idle_delta = current[1] - previous[1]
            time_delta = now - previous_at
            if total_delta <= 0 or time_delta <= 0:
                return None
            busy = total_delta - idle_delta
            return max(0.0, min(100.0, busy * 100.0 / total_delta))
        return None

    def memory(self) -> dict[str, Any]:
        """RAM totals, usage, and process RSS for the active backend."""
        unavailable = {
            "used_bytes": None,
            "total_bytes": None,
            "percent": None,
            "process_rss_bytes": None,
            "source": "unavailable",
        }
        if self.backend == "psutil":
            try:
                virtual = self._psutil.virtual_memory()
                total = int(virtual.total)
                used = total - int(virtual.available)
                rss = int(self._process.memory_info().rss) if self._process is not None else None
            except Exception:
                return unavailable
            return {
                "used_bytes": used,
                "total_bytes": total,
                "percent": float(virtual.percent),
                "process_rss_bytes": rss,
                "source": "psutil",
            }
        if self.backend == "procfs":
            info = _read_proc_meminfo()
            total = info.get("MemTotal")
            if total is None:
                return unavailable
            available = info.get("MemAvailable", info.get("MemFree"))
            used = total - available if available is not None else None
            percent = (used * 100.0 / total) if used is not None and total else None
            return {
                "used_bytes": used,
                "total_bytes": total,
                "percent": percent,
                "process_rss_bytes": _read_proc_self_rss(self._page_size),
                "source": "procfs",
            }
        return unavailable


# ─────────────────────────────────────────────────────────────────────────────
# Collector
# ─────────────────────────────────────────────────────────────────────────────


class RuntimeMetricsCollector:
    """Thread-safe collector with explicit measurement windows and units."""

    def __init__(
        self,
        *,
        latency_window_samples: int = 1000,
        fps_window_seconds: float = 30.0,
        recent_error_limit: int = 10,
        resource_backend: str = "auto",
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        if latency_window_samples < 1:
            raise ValueError("latency_window_samples must be >= 1")
        if fps_window_seconds <= 0:
            raise ValueError("fps_window_seconds must be > 0")
        if recent_error_limit < 0:
            raise ValueError("recent_error_limit must be >= 0")
        self._clock = clock
        self._wall_clock = wall_clock
        self._latency_window_samples = latency_window_samples
        self._fps_window_seconds = fps_window_seconds
        self._recent_error_limit = recent_error_limit
        self._lock = threading.Lock()
        self._resources = ResourceSampler(resource_backend, clock=clock)
        self._hardware = _collect_hardware()
        self._latency: dict[str, deque[float]] = {
            stage: deque(maxlen=latency_window_samples) for stage in LATENCY_STAGES
        }
        self._frame_times: deque[float] = deque(maxlen=FRAME_TIMES_HARD_CAP)
        self._counters: dict[str, int] = {
            "frames_processed": 0,
            "frames_dropped": 0,
            "reconnects": 0,
            "inference_completed": 0,
            "inference_errors": 0,
        }
        self._recent_errors: deque[dict[str, str]] = deque(maxlen=recent_error_limit)
        self._started_at = clock()
        self._started_wall = wall_clock()

    # ── recording ────────────────────────────────────────────────────────────

    def record_latency(self, stage: str, latency_ms: float) -> None:
        """Record one measured latency sample in milliseconds for a stage."""
        if stage not in LATENCY_STAGES:
            raise ValueError(f"Unknown latency stage {stage!r}; expected one of {LATENCY_STAGES}")
        try:
            value = float(latency_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("Latency must be a number of milliseconds") from exc
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("Latency must be a finite, non-negative number of milliseconds")
        with self._lock:
            self._latency[stage].append(value)
            if stage == "inference":
                self._counters["inference_completed"] += 1

    def record_frame(self) -> None:
        """Record a frame that flowed through the pipeline (feeds FPS)."""
        with self._lock:
            now = self._clock()
            self._prune_frame_times(now)
            self._counters["frames_processed"] += 1
            self._frame_times.append(now)

    def record_dropped_frame(self) -> None:
        with self._lock:
            self._counters["frames_dropped"] += 1

    def record_reconnect(self) -> None:
        with self._lock:
            self._counters["reconnects"] += 1

    def record_inference_error(self, message: Any) -> None:
        """Record a failed inference and keep the (truncated) error message."""
        text = str(message)
        if len(text) > ERROR_MESSAGE_LIMIT:
            text = text[: ERROR_MESSAGE_LIMIT - 3] + "..."
        with self._lock:
            self._counters["inference_errors"] += 1
            self._recent_errors.append({"at": _iso(self._wall_clock()), "message": text})

    # ── snapshots ────────────────────────────────────────────────────────────

    def _prune_frame_times(self, now: float) -> None:
        cutoff = now - self._fps_window_seconds
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.popleft()

    def snapshot(self) -> dict[str, Any]:
        """Machine-readable metrics with units, windows, and hardware metadata."""
        with self._lock:
            now = self._clock()
            self._prune_frame_times(now)
            elapsed = max(0.0, now - self._started_at)
            effective_window = min(self._fps_window_seconds, elapsed)
            frames_in_window = len(self._frame_times)
            fps = (frames_in_window / effective_window) if effective_window > 0 else 0.0
            counters = dict(self._counters)
            latency = {stage: _latency_stats(list(self._latency[stage])) for stage in LATENCY_STAGES}
            resources = {
                "backend": self._resources.backend,
                "cpu_percent": self._resources.cpu_percent(),
                "memory": self._resources.memory(),
            }
            recent_errors = [dict(entry) for entry in self._recent_errors]
            gpu = gpu_snapshot()
            window = {
                "started_at": _iso(self._started_wall),
                "elapsed_sec": elapsed,
                "latency_window_samples": self._latency_window_samples,
                "fps_window_seconds": self._fps_window_seconds,
            }
            collected_at = _iso(self._wall_clock())
            hardware = dict(self._hardware)

        drop_total = counters["frames_processed"] + counters["frames_dropped"]
        inference_total = counters["inference_completed"] + counters["inference_errors"]
        return {
            "collected_at": collected_at,
            "window": window,
            "units": dict(UNITS),
            "latency": latency,
            "fps": {
                "value": fps,
                "frames_in_window": frames_in_window,
                "window_seconds": self._fps_window_seconds,
                "effective_window_sec": effective_window,
                "elapsed_sec": elapsed,
            },
            "counters": counters,
            "rates": {
                "frame_drop_rate": (counters["frames_dropped"] / drop_total) if drop_total else 0.0,
                "inference_error_rate": (counters["inference_errors"] / inference_total)
                if inference_total
                else 0.0,
            },
            "resources": resources,
            "gpu": gpu,
            "hardware": hardware,
            "recent_errors": recent_errors,
        }

    def reset(self) -> None:
        """Clear samples and counters (hardware metadata is kept)."""
        with self._lock:
            for stage in LATENCY_STAGES:
                self._latency[stage].clear()
            self._frame_times.clear()
            for key in self._counters:
                self._counters[key] = 0
            self._recent_errors.clear()
            self._started_at = self._clock()
            self._started_wall = self._wall_clock()
            self._resources.reset()


# ─────────────────────────────────────────────────────────────────────────────
# Process-wide collector
# ─────────────────────────────────────────────────────────────────────────────

_runtime_metrics: RuntimeMetricsCollector | None = None
_runtime_metrics_lock = threading.Lock()


def get_runtime_metrics() -> RuntimeMetricsCollector:
    """Return the process-wide runtime metrics collector."""
    global _runtime_metrics
    with _runtime_metrics_lock:
        if _runtime_metrics is None:
            _runtime_metrics = RuntimeMetricsCollector()
        return _runtime_metrics


def reset_runtime_metrics() -> RuntimeMetricsCollector:
    """Replace the process-wide collector (tests and operational resets)."""
    global _runtime_metrics
    with _runtime_metrics_lock:
        _runtime_metrics = RuntimeMetricsCollector()
        return _runtime_metrics
