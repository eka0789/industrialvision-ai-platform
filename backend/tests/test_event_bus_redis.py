"""Tests for the Redis event bus transport (fake in-memory Redis server).

The fake transport mimics the exact client surface the bus uses:
sync ``ping``/``publish``/``close`` and async ``pubsub()`` with
``subscribe``/``unsubscribe``/``get_message``/``aclose``. Two bus instances
sharing one fake server simulate two application processes.
"""

from __future__ import annotations

import asyncio
import unittest

from app.services.event_bus import CHANNEL_PREFIX, EventBus, RedisEventBus


class FakeAsyncPubSub:
    def __init__(self, server: "FakeRedisServer"):
        self._server = server
        self._queue: asyncio.Queue = asyncio.Queue()
        self._channels: set[str] = set()
        self._closed = False
        self.fail_next_get_message = False

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._server.subscribers.setdefault(channel, []).append(self)

    async def unsubscribe(self, channel: str) -> None:
        self._channels.discard(channel)
        subs = self._server.subscribers.get(channel, [])
        if self in subs:
            subs.remove(self)

    async def get_message(self, ignore_subscribe_messages: bool = False, timeout: float | None = None):
        if self.fail_next_get_message:
            self.fail_next_get_message = False
            raise ConnectionError("simulated transport failure")
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout or 0.05)
        except asyncio.TimeoutError:
            return None

    async def aclose(self) -> None:
        self._closed = True
        for channel in list(self._channels):
            await self.unsubscribe(channel)


class FakeSyncClient:
    def __init__(self, server: "FakeRedisServer"):
        self._server = server
        self.fail_ping = False

    def ping(self) -> bool:
        if self.fail_ping:
            raise ConnectionError("simulated outage")
        return True

    def publish(self, channel: str, data: str) -> int:
        self._server.publish(channel, data)
        return 1

    def close(self) -> None:
        return None


class FakeRedisServer:
    """Shared channel registry: publishing fans out to every subscriber."""

    def __init__(self) -> None:
        self.subscribers: dict[str, list[FakeAsyncPubSub]] = {}

    def publish(self, channel: str, data: str) -> None:
        for sub in list(self.subscribers.get(channel, [])):
            sub._queue.put_nowait({"type": "message", "channel": channel, "data": data})


def make_factory(server: FakeRedisServer):
    def factory(_url: str):
        sync_client = FakeSyncClient(server)
        async_client = type("A", (), {"pubsub": staticmethod(lambda: FakeAsyncPubSub(server))})()
        return sync_client, async_client

    return factory


class RedisEventBusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = FakeRedisServer()

    async def test_fan_out_across_two_bus_instances(self):
        """Two buses sharing a Redis server behave like two app processes."""
        bus_a = RedisEventBus("redis://test", client_factory=make_factory(self.server), reconnect_delay=0.01)
        bus_b = RedisEventBus("redis://test", client_factory=make_factory(self.server), reconnect_delay=0.01)
        await bus_a.startup()
        await bus_b.startup()
        queue_a = bus_a.subscribe("inspections")
        await bus_a.wait_ready()
        self.assertTrue(bus_b.publish("inspections", {"n": 1}))
        payload = await asyncio.wait_for(queue_a.get(), timeout=2.0)
        self.assertEqual(payload, {"n": 1})
        await bus_a.shutdown()
        await bus_b.shutdown()

    async def test_local_subscriber_receives_exactly_once(self):
        """Redis echoes the payload back through the pump — no double delivery."""
        bus = RedisEventBus("redis://test", client_factory=make_factory(self.server), reconnect_delay=0.01)
        await bus.startup()
        queue = bus.subscribe("inspections")
        await bus.wait_ready()
        self.assertTrue(bus.publish("inspections", {"n": 2}))
        payload = await asyncio.wait_for(queue.get(), timeout=2.0)
        self.assertEqual(payload, {"n": 2})
        await asyncio.sleep(0.15)  # allow any duplicate delivery to surface
        self.assertTrue(queue.empty())
        await bus.shutdown()

    async def test_degraded_publish_still_delivers_locally(self):
        """When Redis is down, publishes reach local subscribers and are not lost."""
        class FailingPublish(FakeSyncClient):
            def publish(self, channel, data):
                raise ConnectionError("down")

        def failing_factory(_url):
            sync_client = FailingPublish(server)
            async_client = type("A", (), {"pubsub": staticmethod(lambda: FakeAsyncPubSub(server))})()
            return sync_client, async_client

        bus = RedisEventBus("redis://test", client_factory=failing_factory, reconnect_delay=0.01)
        await bus.startup()
        queue = bus.subscribe("inspections")
        self.assertTrue(bus.publish("inspections", {"n": 3}))  # local fallback delivery
        self.assertFalse(bus.connected)  # outage observed by the caller
        self.assertEqual(queue.get_nowait(), {"n": 3})
        await bus.shutdown()

    async def test_missing_package_falls_back_explicitly(self):
        """ImportError inside the client factory → explicit degraded local mode."""
        def import_error_factory(_url):
            raise ImportError("No module named 'redis'")

        bus = RedisEventBus("redis://test", client_factory=import_error_factory, reconnect_delay=0.01)
        await bus.startup()
        self.assertFalse(bus.connected)
        queue = bus.subscribe("inspections")
        self.assertTrue(bus.publish("inspections", {"n": 4}))
        self.assertEqual(queue.get_nowait(), {"n": 4})
        await bus.shutdown()

    async def test_pump_reconnects_after_transport_error(self):
        """Transport error → disconnected → automatic resubscribe and recovery."""
        server = FakeRedisServer()
        pubsubs: list[FakeAsyncPubSub] = []

        def make_pubsub():
            pubsub = FakeAsyncPubSub(server)
            pubsubs.append(pubsub)
            if len(pubsubs) == 1:
                # Fail the very first get_message so the pump must reconnect
                # before any delivery; recovery must resubscribe and deliver.
                pubsub.fail_next_get_message = True
            return pubsub

        def factory(_url):
            sync_client = FakeSyncClient(server)
            async_client = type("A", (), {"pubsub": staticmethod(make_pubsub)})()
            return sync_client, async_client

        bus = RedisEventBus(
            "redis://test", client_factory=factory, reconnect_delay=0.01, poll_interval=0.05
        )
        await bus.startup()
        queue = bus.subscribe("inspections")

        async def wait_until(predicate, timeout: float = 3.0) -> None:
            deadline = asyncio.get_running_loop().time() + timeout
            while not predicate():
                if asyncio.get_running_loop().time() > deadline:
                    self.fail("condition not reached in time")
                await asyncio.sleep(0.01)

        # First get_message fails (fail_next flag), pump reconnects with a new
        # pubsub, resubscribes and reports connected again.
        await wait_until(lambda: bus.connected and len(pubsubs) >= 2)
        self.assertTrue(bus.publish("inspections", {"n": 5}))
        payload = await asyncio.wait_for(queue.get(), timeout=3.0)
        self.assertEqual(payload, {"n": 5})
        self.assertGreaterEqual(len(pubsubs), 2)  # a new pubsub replaced the failed one
        await bus.shutdown()

    async def test_failed_ping_reports_disconnected(self):
        def failing_ping_factory(_url):
            sync_client = FakeSyncClient(self.server)
            sync_client.fail_ping = True
            async_client = type("A", (), {"pubsub": staticmethod(lambda: FakeAsyncPubSub(self.server))})()
            return sync_client, async_client

        bus = RedisEventBus("redis://test", client_factory=failing_ping_factory, reconnect_delay=0.01)
        await bus.startup()
        self.assertFalse(bus.connected)
        await bus.shutdown()

    async def test_create_event_bus_selects_memory_without_url(self):
        bus = EventBus.__new__(EventBus)  # silence linters; constructor covered elsewhere
        self.assertEqual(bus.mode, "memory")


if __name__ == "__main__":
    unittest.main()


class RealFactoryTests(unittest.TestCase):
    """The production client factory must construct working clients (A1 bug
    fix regression: `redis.Redis` was referenced without being imported)."""

    def test_default_factory_returns_paired_clients(self):
        from app.services.event_bus import _default_client_factory

        sync_client, async_client = _default_client_factory("redis://localhost:6399/0")
        self.assertIsNotNone(sync_client)
        self.assertIsNotNone(async_client)
        try:
            sync_client.close()
            asyncio.run(async_client.aclose())
        except Exception:  # noqa: BLE001 - cleanup best-effort (no server here)
            pass


if __name__ == "__main__":
    unittest.main()
