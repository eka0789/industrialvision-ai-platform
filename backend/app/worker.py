"""Standalone RQ worker entry point (``python -m app.worker``).

Consumes the same registered task functions as the in-process pool. Handles
SIGTERM/SIGINT gracefully: the current job finishes, unstarted jobs return to
the queue, and failed jobs land in RQ's failed-job registry (dead-letter).
"""

from __future__ import annotations

import logging
import signal

import redis as redis_module
from rq import Queue, Worker
from rq.job import Job
from rq.suspension import resume as resume_suspension

from app.core.config import settings

# Importing the tasks module registers every task function with the worker.
from app.services import tasks  # noqa: F401  (registration side effect)
from app.services.task_queue import _task_registry

logger = logging.getLogger(__name__)

QUEUE_NAME = "iv"


def main() -> int:
    if not settings.REDIS_URL.strip():
        raise SystemExit("REDIS_URL must be set to run the RQ worker")
    logging.basicConfig(level=settings.LOG_LEVEL.upper())
    connection = redis_module.Redis.from_url(settings.REDIS_URL)
    resume_suspension(connection)
    queue = Queue(QUEUE_NAME, connection=connection)
    logger.info(
        "Starting RQ worker on queue %r with tasks: %s",
        QUEUE_NAME,
        sorted(_task_registry),
    )
    worker = Worker(
        [queue],
        connection=connection,
        job_class=Job,
        name=f"iv-worker-{settings.HOST}",
    )
    # RQ installs SIGTERM/SIGINT handlers that finish the current job and stop
    # cleanly; nothing extra is needed for graceful shutdown.
    worker.work(burst=False, with_scheduler=False)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)  # keep Ctrl+C clean
    raise SystemExit(main())
