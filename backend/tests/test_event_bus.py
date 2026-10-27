"""Tests for the in-process pub/sub event bus."""

from __future__ import annotations

import asyncio
import unittest

from app.services.event_bus import EventBus


class EventBusTests(unittest.TestCase):
    def test_publish_without_subscribers_returns_false(self):
        bus = EventBus()
        self.assertFalse(bus.publish("inspections", {"hello": 1}))

    def test_subscriber_receives_published_payload(self):
        bus = EventBus()
        queue = bus.subscribe("inspections")
        self.assertTrue(bus.publish("inspections", {"value": 42}))
        self.assertEqual(queue.get_nowait(), {"value": 42})

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        first, second = bus.subscribe("inspections"), bus.subscribe("inspections")
        bus.publish("inspections", {"n": 1})
        self.assertEqual(first.get_nowait()["n"], 1)
        self.assertEqual(second.get_nowait()["n"], 1)
        self.assertEqual(bus.subscriber_count("inspections"), 2)

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        queue = bus.subscribe("inspections")
        bus.unsubscribe("inspections", queue)
        self.assertFalse(bus.publish("inspections", {"n": 2}))
        self.assertTrue(queue.empty())
        self.assertEqual(bus.subscriber_count("inspections"), 0)

    def test_overflow_drops_oldest(self):
        bus = EventBus(queue_size=2)
        queue = bus.subscribe("inspections")
        for value in range(4):
            bus.publish("inspections", {"n": value})
        self.assertEqual([queue.get_nowait()["n"] for _ in range(queue.qsize())], [2, 3])

    def test_topics_are_isolated(self):
        bus = EventBus()
        queue = bus.subscribe("cameras")
        bus.publish("inspections", {"n": 3})
        self.assertTrue(queue.empty())


if __name__ == "__main__":
    unittest.main()
