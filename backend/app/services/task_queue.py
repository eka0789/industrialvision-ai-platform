"""Background task queue with two interchangeable transports.

The platform keeps one registry of named task functions
(:mod:`app.services.tasks`); both transports execute exactly those functions:

- :class:`InProcessTaskQueue` — bounded thread pool with retries (linear
  backoff) and a bounded dead-letter record after the final failure. This is
  the development default and the degraded fallback when Redis/`rq` are
  unavailable, so callers never silently lose work.

- :class:`RQTaskQueue` — Redis-backed RQ queue for the standalone worker
  service (``python -m app.worker``). Failed jobs land in RQ's failed-job
  registry (the dead-letter store) after ``max_retries`` attempts.

The active transport is selected from ``TASK_QUEUE_MODE``
(``in-process`` | ``rq`` | ``auto``). Production requires ``rq`` (validated in
config); if the Redis connection drops at enqueue time the facade degrades to
in-process execution with an explicit log and a health-visible flag — a task
is never dropped without a trace.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_DEAD_LETTER_SIZE = 256

_task_registry: dict[str, Callable[..., Any]] = {}


def register_task(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a task function under a unique name for both transports."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        existing = _task_registry.get(name)
        if existing is not None and existing is not fn:
            raise ValueError(f"Task {name!r} is already registered by a different function")
        _task_registry[name] = fn
        fn.task_name = name  # type: ignore[attr-defined]
        return fn

    return decorator


def resolve_task(name: str) -> Callable[..., Any]:
    """Look up a registered task; unknown names fail fast (no silent drop)."""
    try:
        return _task_registry[name]
    except KeyError:
        raise ValueError(f"Unknown task {name!r}; registered: {sorted(_task_registry)}") from None


@dataclass
class DeadLetter:
    """Structured record of a task that exhausted its retries."""

    task_name: str
    args: tuple
    kwargs: dict
    attempts: int
    error: str
    failed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "task_name": self.task_name,
            "args": list(self.args),
            "kwargs": self.kwargs,
            "attempts": self.attempts,
            "error": self.error,
            "failed_at": self.failed_at,
        }


class InProcessTaskQueue:
    """Thread-pool executor with retries and a dead-letter record."""

    mode = "in-process"

    def __init__(
        self,
        max_workers: int = 4,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        dead_letter_size: int = DEFAULT_DEAD_LETTER_SIZE,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="iv-task")
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._dead_letters: deque[DeadLetter] = deque(maxlen=dead_letter_size)
        self._futures: dict[str, Future] = {}
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, Any] = {}
        self._outcomes: dict[str, str] = {}  # "completed" | "dead-lettered"
        self._lock = threading.Lock()
        self._retry_timers: set[threading.Timer] = set()
        self._stopped = False

    # -- health ------------------------------------------------------------

    @property
    def degraded(self) -> bool:
        return False

    def dead_letter_count(self) -> int:
        return len(self._dead_letters)

    def dead_letters(self) -> list[dict]:
        return [entry.as_dict() for entry in self._dead_letters]

    def pending_count(self) -> int:
        return sum(1 for future in list(self._futures.values()) if not future.done())

    # -- queue interface ---------------------------------------------------

    def enqueue(self, name: str, *args: Any, **kwargs: Any) -> str:
        """Run a registered task on the pool; returns the job id."""
        if self._stopped:
            raise RuntimeError("Task queue is shut down")
        fn = resolve_task(name)
        job_id = uuid.uuid4().hex
        with self._lock:
            self._events[job_id] = threading.Event()
        future = self._executor.submit(self._run, job_id, name, fn, 1, args, kwargs)
        with self._lock:
            self._futures[job_id] = future
        return job_id

    def _run(self, job_id: str, name: str, fn: Callable[..., Any], attempt: int, args: tuple, kwargs: dict) -> Any:
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - task failures must not kill the pool
            if attempt <= self._max_retries:
                delay = self._retry_delay * attempt
                logger.warning(
                    "Task %s (job %s) failed (attempt %d/%d): %s; retrying in %.1fs",
                    name, job_id, attempt, self._max_retries + 1, exc, delay,
                )
                timer = threading.Timer(
                    delay, self._resubmit, args=(job_id, name, fn, attempt + 1, args, kwargs)
                )
                timer.daemon = True
                with self._lock:
                    if self._stopped:
                        self._finalize(job_id, None, "dead-lettered")
                        self._dead_letter(job_id, name, args, kwargs, attempt, exc)
                        return None
                    self._retry_timers.add(timer)
                timer.start()
                return None
            logger.error(
                "Task %s (job %s) dead-lettered after %d attempts: %s",
                name, job_id, attempt, exc,
            )
            self._dead_letter(job_id, name, args, kwargs, attempt, exc)
            self._finalize(job_id, None, "dead-lettered")
            return None
        self._finalize(job_id, result, "completed")
        return result

    def _finalize(self, job_id: str, result: Any, outcome: str) -> None:
        with self._lock:
            self._results[job_id] = result
            self._outcomes[job_id] = outcome
            event = self._events.get(job_id)
        if event is not None:
            event.set()

    def _resubmit(self, job_id: str, name: str, fn: Callable[..., Any], attempt: int, args: tuple, kwargs: dict) -> None:
        with self._lock:
            self._retry_timers.discard(threading.current_thread())
            if self._stopped:
                return
        future = self._executor.submit(self._run, job_id, name, fn, attempt, args, kwargs)
        with self._lock:
            self._futures[job_id] = future

    def _dead_letter(self, job_id: str, name: str, args: tuple, kwargs: dict, attempts: int, exc: Exception) -> None:
        entry = DeadLetter(
            task_name=name,
            args=args,
            kwargs=kwargs,
            attempts=attempts,
            error=f"{type(exc).__name__}: {exc}"[:1000],
        )
        self._dead_letters.append(entry)

    # -- test/ops helpers ---------------------------------------------------

    def wait_for(self, job_id: str, timeout: float = 30.0) -> Any:
        """Block until the job (including retries) settles; returns its result.

        Raises :class:`KeyError` for unknown job ids and
        :class:`TimeoutError` if the job does not settle in time.
        """
        with self._lock:
            event = self._events.get(job_id)
        if event is None:
            raise KeyError(job_id)
        if not event.wait(timeout):
            raise TimeoutError(f"Job {job_id} did not settle within {timeout}s")
        with self._lock:
            return self._results.get(job_id)

    def shutdown(self, wait: bool = True) -> None:
        """Graceful shutdown: stop accepting work, finish running jobs."""
        with self._lock:
            self._stopped = True
            timers = list(self._retry_timers)
            self._retry_timers.clear()
        for timer in timers:
            timer.cancel()
        self._executor.shutdown(wait=wait)


class RQTaskQueue:
    """RQ transport backed by Redis for the standalone worker service."""

    mode = "rq"

    def __init__(
        self,
        redis_url: str | None = None,
        queue_name: str = "iv",
        max_retries: int = DEFAULT_MAX_RETRIES,
        connection: Any = None,
    ) -> None:
        import redis as redis_module
        from rq import Queue, Retry

        self._queue_name = queue_name
        self._max_retries = max_retries
        self._Retry = Retry
        self._connection = connection or redis_module.Redis.from_url(
            redis_url or settings.REDIS_URL,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
            decode_responses=False,
        )
        self._queue = Queue(queue_name, connection=self._connection)

    @property
    def degraded(self) -> bool:
        return False

    def dead_letter_count(self) -> int:
        return len(self._queue.failed_job_registry)

    def enqueue(self, name: str, *args: Any, **kwargs: Any) -> str:
        fn = resolve_task(name)
        job = self._queue.enqueue(
            fn,
            *args,
            retry=self._Retry(max=self._max_retries),
            **kwargs,
        )
        return job.id

    def pending_count(self) -> int:
        return self._queue.count

    def shutdown(self, wait: bool = True) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


class TaskQueueFacade:
    """Selects the transport at construction; degrades visibly at enqueue time.

    When the RQ transport cannot enqueue (Redis outage), the job runs on the
    in-process pool instead and :attr:`degraded` turns True until a successful
    RQ enqueue recovers — work is delayed, never dropped.
    """

    def __init__(self, primary: InProcessTaskQueue | RQTaskQueue, fallback: InProcessTaskQueue) -> None:
        self._primary = primary
        self._fallback = fallback
        self._degraded = False

    @property
    def mode(self) -> str:
        return self._primary.mode

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def primary(self) -> InProcessTaskQueue | RQTaskQueue:
        return self._primary

    def enqueue(self, name: str, *args: Any, **kwargs: Any) -> str:
        try:
            job_id = self._primary.enqueue(name, *args, **kwargs)
            if self._degraded:
                logger.info("RQ enqueue recovered; task queue no longer degraded")
            self._degraded = False
            return job_id
        except Exception as exc:  # noqa: BLE001 - Redis outage or transport error
            if self._primary is self._fallback:
                raise
            logger.error("RQ enqueue failed (%s); running task in-process (degraded)", exc)
            self._degraded = True
            return self._fallback.enqueue(name, *args, **kwargs)

    def wait_for(self, job_id: str, timeout: float = 30.0) -> Any:
        return self._fallback.wait_for(job_id, timeout=timeout)

    def dead_letter_count(self) -> int:
        if isinstance(self._primary, RQTaskQueue) and not self._degraded:
            try:
                return self._primary.dead_letter_count()
            except Exception:  # noqa: BLE001
                return self._fallback.dead_letter_count()
        return self._fallback.dead_letter_count()

    def pending_count(self) -> int:
        try:
            return self._primary.pending_count()
        except Exception:  # noqa: BLE001
            return self._fallback.pending_count()

    def shutdown(self, wait: bool = True) -> None:
        self._primary.shutdown(wait=wait)
        if self._fallback is not self._primary:
            self._fallback.shutdown(wait=wait)


def create_task_queue(config: Any = None) -> TaskQueueFacade:
    """Build the task queue from settings (explicit mode selection)."""
    config = config or settings
    mode = getattr(config, "TASK_QUEUE_MODE", "in-process")
    in_process = InProcessTaskQueue(
        max_retries=getattr(config, "TASK_MAX_RETRIES", DEFAULT_MAX_RETRIES),
        retry_delay=getattr(config, "TASK_RETRY_DELAY_SECONDS", DEFAULT_RETRY_DELAY),
    )
    if mode in ("rq", "auto") and getattr(config, "REDIS_URL", "").strip():
        try:
            primary: InProcessTaskQueue | RQTaskQueue = RQTaskQueue(
                redis_url=config.REDIS_URL,
                max_retries=getattr(config, "TASK_MAX_RETRIES", DEFAULT_MAX_RETRIES),
            )
        except Exception as exc:  # ImportError (no rq package) or connection error
            if mode == "rq":
                raise RuntimeError(f"TASK_QUEUE_MODE=rq but RQ/Redis is unavailable: {exc}") from exc
            logger.warning("RQ unavailable (%s); task queue runs in-process", exc)
            primary = in_process
    else:
        primary = in_process
    return TaskQueueFacade(primary, in_process)


task_queue: TaskQueueFacade = create_task_queue()
