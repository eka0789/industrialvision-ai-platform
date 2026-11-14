"""Prometheus metrics (``iv_`` namespace) for the existing counters.

Exposure contract:
- ``/metrics`` is plain-text Prometheus exposition, unauthenticated by design
  and reachable only on the internal Docker network (nginx never proxies it).
- Every series is prefixed ``iv_``; units follow Prometheus conventions
  (seconds, bytes, ratios). Buckets cover the documented inspection budget.
- Gauges that reflect live component state (event bus, worker, DB) are read
  at scrape time from the running objects, so the dashboard can never show a
  stale snapshot of those.
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# A dedicated registry keeps the exposition stable even if a future test
# imports the module twice.
registry = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "iv_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=registry,
)
HTTP_DURATION = Histogram(
    "iv_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=registry,
)
INSPECTION_LATENCY = Histogram(
    "iv_inspection_latency_seconds",
    "Inspection pipeline latency (model + decision) in seconds",
    ["camera_id"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=registry,
)
DECISIONS = Counter(
    "iv_inspection_decisions_total",
    "Inspection decision outcomes",
    ["decision"],
    registry=registry,
)
WS_CONNECTIONS = Gauge(
    "iv_websocket_connections",
    "Currently open WebSocket connections",
    ["endpoint"],
    registry=registry,
)
WS_CONNECTIONS_OPENED = Counter(
    "iv_websocket_connections_opened",
    "WebSocket connections opened",
    ["endpoint"],
    registry=registry,
)
EVENT_BUS_CONNECTED = Gauge(
    "iv_event_bus_connected",
    "1 when the event bus is connected (memory bus is always 1)",
    registry=registry,
)
WORKER_DEGRADED = Gauge(
    "iv_worker_degraded",
    "1 when background tasks are running in-process because RQ enqueue failed",
    registry=registry,
)
TASKS_DEAD_LETTERED = Gauge(
    "iv_tasks_dead_lettered",
    "Tasks that exhausted their retries",
    registry=registry,
)
MODEL_DRIFT_PSI = Gauge(
    "iv_model_drift_psi",
    "Latest PSI per (model, feature) from the scheduled drift check",
    ["model", "feature"],
    registry=registry,
)
DB_OK = Gauge(
    "iv_database_ok",
    "1 when the database connection check succeeds at scrape time",
    registry=registry,
)

_HTTP_TEMPLATE_METRICS = ("iv_http_requests_total", "iv_http_request_duration_seconds_count")


def render_metrics() -> bytes:
    """Render the exposition, refreshing component-state gauges first."""
    from app.api.v1.routers.health import _db_ok
    from app.services.event_bus import event_bus
    from app.services.task_queue import task_queue

    EVENT_BUS_CONNECTED.set(1 if event_bus.connected else 0)
    WORKER_DEGRADED.set(1 if task_queue.degraded else 0)
    TASKS_DEAD_LETTERED.set(task_queue.dead_letter_count())
    DB_OK.set(1 if _db_ok() else 0)
    return generate_latest(registry)


def observe_http(endpoint: str, method: str, status: int, duration_seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    HTTP_DURATION.labels(endpoint=endpoint).observe(duration_seconds)


def record_decision(decision: str, latency_seconds: float | None = None, camera_id: int | None = None) -> None:
    DECISIONS.labels(decision=decision).inc()
    if latency_seconds is not None and camera_id is not None:
        INSPECTION_LATENCY.labels(camera_id=str(camera_id)).observe(latency_seconds)


def ws_opened(endpoint: str) -> None:
    WS_CONNECTIONS.labels(endpoint=endpoint).inc()
    WS_CONNECTIONS_OPENED.labels(endpoint=endpoint).inc()


def ws_closed(endpoint: str) -> None:
    WS_CONNECTIONS.labels(endpoint=endpoint).dec()
