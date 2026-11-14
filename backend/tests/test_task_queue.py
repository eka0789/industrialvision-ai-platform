"""Task queue tests: in-process execution, retries, dead-letter, RQ transport.

Task functions are module-level (RQ workers refuse ``__main__``/closure
functions) and registered once at import time.
"""

from __future__ import annotations

import threading
import time
import unittest

from app.services.task_queue import (
    InProcessTaskQueue,
    RQTaskQueue,
    TaskQueueFacade,
    register_task,
)

_counter_lock = threading.Lock()


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def bump(self) -> int:
        with _counter_lock:
            self.calls += 1
            return self.calls


counters: dict[str, _Counter] = {}


def _counter_for(key: str) -> _Counter:
    return counters.setdefault(key, _Counter())


def task_ok(value=1):
    return value


def task_event(seen: threading.Event):
    seen.set()


def task_fail_always():
    _counter_for("fail_always").bump()
    raise RuntimeError("boom")


def task_flaky():
    calls = _counter_for("flaky").bump()
    if calls < 2:
        raise RuntimeError("transient")
    return "ok"


def task_block(release: threading.Event, finished: threading.Event):
    release.wait(5)
    finished.set()


def task_rq_fail():
    _counter_for("rq_fail").bump()
    raise RuntimeError("boom-rq")


def task_rq_work(value):
    return value * 2


register_task("tq_ok")(task_ok)
register_task("tq_event")(task_event)
register_task("tq_fail")(task_fail_always)
register_task("tq_flaky")(task_flaky)
register_task("tq_block")(task_block)
register_task("tq_rq_fail")(task_rq_fail)
register_task("tq_rq_work")(task_rq_work)


def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.02)


class InProcessTaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = InProcessTaskQueue(max_workers=2, max_retries=2, retry_delay=0.05)
        self.addCleanup(self.queue.shutdown)

    def test_task_executes_via_queue(self):
        seen = threading.Event()
        job_id = self.queue.enqueue("tq_event", seen)
        self.assertTrue(seen.wait(5))
        self.queue.wait_for(job_id, timeout=5)

    def test_enqueue_returns_result(self):
        job_id = self.queue.enqueue("tq_ok", value=42)
        self.assertEqual(self.queue.wait_for(job_id), 42)

    def test_duplicate_task_name_rejected_for_different_function(self):
        def other():
            return None

        with self.assertRaises(ValueError):
            register_task("tq_ok")(other)

    def test_unknown_task_fails_fast(self):
        with self.assertRaises(ValueError):
            self.queue.enqueue("no_such_task")

    def test_failure_retries_then_dead_letters(self):
        job_id = self.queue.enqueue("tq_fail")
        # max_retries=2 → initial attempt + 2 retries = 3 attempts, then dead-letter.
        _wait_until(lambda: self.queue.dead_letter_count() == 1)
        self.assertGreaterEqual(_counter_for("fail_always").calls, 3)
        entry = self.queue.dead_letters()[0]
        self.assertEqual(entry["task_name"], "tq_fail")
        self.assertGreaterEqual(entry["attempts"], 3)
        self.assertIn("boom", entry["error"])
        self.assertEqual(self.queue.wait_for(job_id, timeout=10), None)
        self.assertEqual(self.queue._outcomes.get(job_id), "dead-lettered")

    def test_retry_recovers_on_second_attempt(self):
        job_id = self.queue.enqueue("tq_flaky")
        self.assertEqual(self.queue.wait_for(job_id, timeout=10), "ok")

    def test_graceful_shutdown_drains_running_jobs(self):
        release, finished = threading.Event(), threading.Event()
        self.queue.enqueue("tq_block", release, finished)
        release.set()
        self.queue.shutdown(wait=True)
        self.assertTrue(finished.is_set())
        self.queue._executor.shutdown(wait=False)  # cleanup already done


class RQTaskQueueTests(unittest.TestCase):
    """RQ transport exercised against fakeredis (no Redis server required)."""

    def setUp(self):
        try:
            import fakeredis  # noqa: F401
            import rq  # noqa: F401
        except ImportError:
            self.skipTest("rq/fakeredis not installed")
        import fakeredis

        self.connection = fakeredis.FakeStrictRedis()
        self.queue = RQTaskQueue(connection=self.connection, queue_name="iv-test", max_retries=2)
        self.addCleanup(self.queue.shutdown)

    def _drain(self, times: int = 1):
        from rq import SimpleWorker

        worker = SimpleWorker([self.queue._queue], connection=self.connection)
        for _ in range(times):
            worker.work(burst=True)

    def test_enqueue_and_execute_via_worker(self):
        from rq.job import Job

        job_id = self.queue.enqueue("tq_rq_work", 21)
        self._drain()
        job = Job.fetch(job_id, connection=self.connection)
        self.assertEqual(job.result, 42)

    def test_failure_lands_in_dead_letter_registry(self):
        self.queue.enqueue("tq_rq_fail")
        # 1 initial + 2 retries → 3 worker passes before the dead registry.
        self._drain(times=3)
        self.assertEqual(self.queue.dead_letter_count(), 1)

    def test_unknown_task_rejected_before_enqueue(self):
        with self.assertRaises(ValueError):
            self.queue.enqueue("no_such_rq_task")


class FacadeTests(unittest.TestCase):
    def test_facade_degrades_to_in_process_when_rq_enqueue_fails(self):
        seen = threading.Event()

        def facade_work():
            seen.set()

        register_task("tq_facade")(facade_work)

        class BrokenPrimary:
            mode = "rq"

            def enqueue(self, name, *args, **kwargs):
                raise ConnectionError("redis down")

            def shutdown(self, wait=True):
                return None

        fallback = InProcessTaskQueue(max_retries=0)
        self.addCleanup(fallback.shutdown)
        facade = TaskQueueFacade(BrokenPrimary(), fallback)
        job_id = facade.enqueue("tq_facade")
        self.assertTrue(seen.wait(5))
        self.assertTrue(facade.degraded)
        fallback.wait_for(job_id, timeout=5)

    def test_facade_recovers_flag_after_successful_rq_enqueue(self):
        class HealthyPrimary:
            mode = "rq"

            def enqueue(self, name, *args, **kwargs):
                return "job-1"

            def shutdown(self, wait=True):
                return None

        fallback = InProcessTaskQueue()
        self.addCleanup(fallback.shutdown)
        facade = TaskQueueFacade(HealthyPrimary(), fallback)
        facade._degraded = True
        self.assertEqual(facade.enqueue("tq_ok", value=1), "job-1")
        self.assertFalse(facade.degraded)


if __name__ == "__main__":
    unittest.main()
