"""In-process pub/sub event bus for real-time WebSocket fan-out.

Producers call :meth:`EventBus.publish` from the asyncio event loop thread;
each subscriber receives payloads on its own bounded queue. On overflow the
oldest queued payload is dropped so a slow client never blocks producers.
"""

from __future__ import annotations

import asyncio
from typing import Any

DEFAULT_QUEUE_SIZE = 256


class EventBus:
    """Topic-based broadcast bus with bounded per-subscriber queues."""

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = max(1, queue_size)
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, topic: str) -> asyncio.Queue:
        """Register a bounded queue for a topic and return it."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.setdefault(topic, set()).add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        """Remove a previously subscribed queue; unknown pairs are ignored."""
        subscribers = self._subscribers.get(topic)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(topic, None)

    def publish(self, topic: str, payload: Any) -> bool:
        """Deliver a payload to every subscriber; returns True if anyone listened.

        Must be called from the event loop thread. Payloads must already be
        JSON-serializable (use ``jsonable_encoder``/``mode="json"`` upstream).
        """
        subscribers = self._subscribers.get(topic)
        if not subscribers:
            return False
        for queue in tuple(subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race with consumer
                    continue
            queue.put_nowait(payload)
        return True

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, ()))


event_bus = EventBus()
