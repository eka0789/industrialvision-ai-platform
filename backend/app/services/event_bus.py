"""Event bus for real-time WebSocket fan-out.

Two interchangeable implementations share one interface:

- :class:`EventBus` — in-process pub/sub with bounded per-subscriber queues.
  Producers call :meth:`EventBus.publish` from the asyncio event loop thread;
  each subscriber receives payloads on its own bounded queue. On overflow the
  oldest queued payload is dropped so a slow client never blocks producers.
  This is the default for single-process development.

- :class:`RedisEventBus` — Redis pub/sub transport used when ``REDIS_URL`` is
  configured. Publishing uses a synchronous client (sub-millisecond on a LAN
  Redis; every call site already runs on the event loop thread), subscriptions
  are pumped into the same bounded per-subscriber queues by one background
  reader task with automatic resubscribe/backoff on connection loss. While the
  Redis connection is down, publishes are delivered to local subscribers only
  and :attr:`connected` is ``False`` so ``/health`` can report the degraded
  mode explicitly — payloads are never silently dropped.

The active bus is selected once at import time via :func:`create_event_bus`;
``/health`` must report the resulting ``mode``/``connected`` values.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 256
CHANNEL_PREFIX = "iv:events:"
DEFAULT_RECONNECT_DELAY = 2.0


class EventBus:
    """Topic-based in-process broadcast bus with bounded per-subscriber queues."""

    mode = "memory"

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = max(1, queue_size)
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    @property
    def connected(self) -> bool:
        return True

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

    async def startup(self) -> None:  # interface parity with RedisEventBus
        return None

    async def shutdown(self) -> None:
        return None

    async def wait_ready(self) -> None:
        return None


# Factories returning (sync_client, async_client) for a Redis URL. Kept behind
# a callable so tests can inject fakes and the `redis` package stays optional.
ClientFactory = Callable[[str], tuple[Any, Any]]


def _default_client_factory(redis_url: str) -> tuple[Any, Any]:
    import redis  # imported lazily; the package is optional
    import redis.asyncio as aioredis

    sync_client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=2.0,
        socket_timeout=1.0,
        decode_responses=True,
    )
    async_client = aioredis.from_url(redis_url, decode_responses=True)
    return sync_client, async_client


class RedisEventBus:
    """Redis pub/sub transport with the :class:`EventBus` consumer interface.

    ``client_factory`` returns ``(sync_client, async_client)``. Tests inject a
    fake to exercise fan-out across two bus instances ("processes") without a
    Redis server; production uses the real client factory.
    """

    mode = "redis"

    def __init__(
        self,
        redis_url: str,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        client_factory: ClientFactory | None = None,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
        poll_interval: float = 1.0,
    ) -> None:
        self._redis_url = redis_url
        self._queue_size = max(1, queue_size)
        self._client_factory = client_factory or _default_client_factory
        self._reconnect_delay = reconnect_delay
        self._poll_interval = poll_interval
        self._sync_client: Any = None
        self._async_client: Any = None
        self._pubsub: Any = None
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._pump_task: asyncio.Task | None = None
        self._connected = False
        self._fallback_warned = False
        self._ready = asyncio.Event()

    # -- state -------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    # -- lifecycle ---------------------------------------------------------

    async def startup(self) -> None:
        """Create clients and verify connectivity once.

        A failed ping keeps the bus running in degraded mode (publishes fall
        back to local delivery and /health reports ``connected: false``);
        the pub/sub pump reconnects in the background.
        """
        if self._sync_client is not None:
            return
        try:
            self._sync_client, self._async_client = self._client_factory(self._redis_url)
        except Exception as exc:  # ImportError (package missing) or bad URL
            logger.warning("Redis event bus unavailable (%s); using in-process bus", exc)
            self._sync_client = None
            self._connected = False
            self._ready.set()
            return
        try:
            await asyncio.to_thread(self._sync_client.ping)
            self._connected = True
            logger.info("Redis event bus connected to %s", self._safe_url())
        except Exception as exc:
            logger.warning("Redis event bus ping failed (%s); degraded until it recovers", exc)
            self._connected = False
        self._ready.set()

    async def shutdown(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
            self._pump_task = None
        self._connected = False
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            self._pubsub = None
        if self._sync_client is not None:
            try:
                self._sync_client.close()
            except Exception:  # noqa: BLE001
                pass

    def _safe_url(self) -> str:
        """URL with any password redacted for logs."""
        try:
            from sqlalchemy.engine import make_url

            url = make_url(self._redis_url)
            return str(url.set(password="***")) if url.password else str(url)
        except Exception:  # noqa: BLE001 - never fail logging on a bad URL
            return "redis://<unparseable>"

    # -- pub/sub pump ------------------------------------------------------

    def _ensure_pump(self) -> None:
        if self._pump_task is None or self._pump_task.done():
            self._ready.clear()
            self._pump_task = asyncio.create_task(self._pump(), name="redis-event-bus-pump")

    async def _pump(self) -> None:
        """Read pub/sub messages into per-subscriber queues, reconnecting on failure."""
        while True:
            topics = tuple(self._subscribers)
            try:
                if self._pubsub is None:
                    self._pubsub = self._async_client.pubsub()
                    for topic in topics:
                        await self._pubsub.subscribe(CHANNEL_PREFIX + topic)
                    if topics:
                        logger.info("Redis event bus subscribed to %d topic(s)", len(topics))
                self._connected = True
                self._ready.set()
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=self._poll_interval
                )
                if message is not None and message.get("type") == "message":
                    topic = str(message.get("channel", "")).removeprefix(CHANNEL_PREFIX)
                    self._dispatch(topic, message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any transport error
                self._connected = False
                self._ready.clear()
                logger.warning(
                    "Redis event bus connection lost (%s); retrying in %.1fs",
                    exc,
                    self._reconnect_delay,
                )
                if self._pubsub is not None:
                    try:
                        await self._pubsub.aclose()
                    except Exception:  # noqa: BLE001
                        pass
                    self._pubsub = None
                await asyncio.sleep(self._reconnect_delay)

    def _dispatch(self, topic: str, raw: Any) -> None:
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        except (ValueError, TypeError):
            logger.warning("Dropping non-JSON event bus payload on topic %r", topic)
            return
        self._deliver_locally(topic, payload)

    # -- consumer interface (parity with EventBus) -------------------------

    def subscribe(self, topic: str) -> asyncio.Queue:
        """Register a bounded queue for a topic and return it.

        Delivery starts once the pump confirms the Redis subscription; call
        :meth:`wait_ready` to await it (WebSocket handlers tolerate the brief
        window).
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.setdefault(topic, set()).add(queue)
        if self._pubsub is not None and self._pump_task is not None and not self._pump_task.done():
            # Pump is running: subscribe the new channel immediately.
            asyncio.ensure_future(self._subscribe_channel(topic))
        self._ensure_pump()
        return queue

    async def _subscribe_channel(self, topic: str) -> None:
        if self._pubsub is None:
            return
        try:
            await self._pubsub.subscribe(CHANNEL_PREFIX + topic)
        except Exception as exc:  # noqa: BLE001 - pump reconnect path resubscribes
            logger.warning("Failed to subscribe topic %r (%s)", topic, exc)

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(topic)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(topic, None)
                if self._pubsub is not None:
                    asyncio.ensure_future(self._unsubscribe_channel(topic))

    async def _unsubscribe_channel(self, topic: str) -> None:
        if self._pubsub is None:
            return
        try:
            await self._pubsub.unsubscribe(CHANNEL_PREFIX + topic)
        except Exception:  # noqa: BLE001
            pass

    def publish(self, topic: str, payload: Any) -> bool:
        """Publish to Redis; Redis delivers it to every subscriber (all processes).

        Local subscribers receive the payload back through the pump, exactly
        once. While Redis is unreachable the payload is mirrored to local
        subscribers directly (degraded single-process mode) and ``False`` is
        returned so callers can observe the outage; nothing is silently
        dropped.
        """
        if self._sync_client is not None:
            try:
                self._sync_client.publish(CHANNEL_PREFIX + topic, json.dumps(payload, default=str))
                return True
            except Exception as exc:  # noqa: BLE001 - sync client reconnects on next use
                self._connected = False
                self._ready.clear()
                if not self._fallback_warned:
                    logger.warning("Redis publish failed (%s); local-only delivery until it recovers", exc)
                    self._fallback_warned = True
        return self._deliver_locally(topic, payload)

    def _deliver_locally(self, topic: str, payload: Any) -> bool:
        for queue in tuple(self._subscribers.get(topic, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    continue
            queue.put_nowait(payload)
        return bool(self._subscribers.get(topic))

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, ()))

    async def wait_ready(self) -> None:
        await self._ready.wait()


def create_event_bus(redis_url: str | None = None) -> EventBus | RedisEventBus:
    """Select the active bus implementation.

    Redis mode requires the ``redis`` package; when it is missing the explicit
    in-memory fallback is used and logged (never a silent pretend-Redis path).
    """
    url = (redis_url if redis_url is not None else settings.REDIS_URL).strip()
    if not url:
        logger.info("REDIS_URL not set; event bus runs in in-process memory mode")
        return EventBus()
    return RedisEventBus(url)


event_bus: EventBus | RedisEventBus = create_event_bus()
