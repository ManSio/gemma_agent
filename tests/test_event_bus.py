"""Тесты EventBus v2: async-шина, typed events, healers."""
import asyncio
import os
import unittest
from typing import Any, Dict

from core.event_bus import bus, EventBus, ModuleExecutedEvent, ModuleFailedEvent


class TestEventBus(unittest.TestCase):
    def setUp(self):
        # Сброс шины: очищаем историю и подписчиков
        bus._event_history.clear()
        bus._subscribers.clear()
        bus._async_subs.clear()
        bus._filters.clear()

    def test_emit_sync_callback(self):
        results = []

        def cb(payload: Dict[str, Any]):
            results.append(payload)

        bus.subscribe("test.event", cb)
        bus.emit("test.event", {"foo": "bar"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("foo"), "bar")
        self.assertIn("event_type", results[0])
        self.assertIn("ts", results[0])
        self.assertIn("correlation_id", results[0])

    def test_emit_wildcard(self):
        results = []

        def cb(payload: Dict[str, Any]):
            results.append(payload.get("event_type"))

        bus.subscribe("*", cb)
        bus.emit("wild.test", {})
        bus.emit("another.test", {})

        self.assertEqual(results, ["wild.test", "another.test"])

    def test_emit_filter_rejects(self):
        results = []

        def cb(payload):
            results.append(payload)

        bus.subscribe("filtered.event", cb)
        bus.add_filter("filtered.event", lambda p: False)
        bus.emit("filtered.event", {})
        self.assertEqual(len(results), 0)

    def test_unsubscribe(self):
        results = []

        def cb(payload):
            results.append(payload)

        bus.subscribe("unsub.event", cb)
        bus.emit("unsub.event", {"a": 1})
        self.assertEqual(len(results), 1)
        bus.unsubscribe("unsub.event", cb)
        bus.emit("unsub.event", {"a": 2})
        self.assertEqual(len(results), 1)  # не увеличился

    def test_history_limited(self):
        for i in range(10):
            bus.emit("hist.event", {"i": i})
        self.assertEqual(len(bus.history()), 10)
        # Проверяем, что n=3 возвращает 3
        self.assertEqual(len(bus.history(n=3)), 3)

    def test_history_filter_by_type(self):
        bus.emit("type.a", {})
        bus.emit("type.b", {})
        bus.emit("type.a", {})
        hist = bus.history(event_type="type.a")
        self.assertEqual(len(hist), 2)
        for e in hist:
            self.assertEqual(e.event_type, "type.a")

    def test_subscriber_count(self):
        def cb(p):
            pass

        bus.subscribe("foo", cb)
        bus.subscribe("bar", cb)
        bus.subscribe_async("baz", lambda p: None)
        cnt = bus.subscriber_count()
        self.assertEqual(cnt.get("foo"), 1)
        self.assertEqual(cnt.get("bar"), 1)
        self.assertEqual(cnt.get("baz"), 1)

    def test_snapshot(self):
        snap = bus.snapshot()
        self.assertIn("total_events_seen", snap)
        self.assertIn("subscribers", snap)

    def test_emit_error_in_callback_does_not_crash(self):
        def cb(payload):
            raise ValueError("oops")

        bus.subscribe("crash.event", cb)
        # Не должно бросить исключение
        bus.emit("crash.event", {})

    def test_event_dataclass_schema_executed(self):
        ev = ModuleExecutedEvent(module_name="test", duration_ms=12.3, ok=True)
        self.assertEqual(ev.module_name, "test")
        self.assertTrue(ev.ok)

    def test_event_dataclass_schema_failed(self):
        ev = ModuleFailedEvent(module_name="boom", error="crash", traceback="tb...")
        self.assertEqual(ev.module_name, "boom")
        self.assertIn("crash", ev.error)

    def test_correlation_id_unique_per_emit(self):
        ids = set()
        for _ in range(100):
            bus.emit("corr.test", {})
            last = bus.history(n=1)[-1]
            ids.add(last.correlation_id)
        self.assertEqual(len(ids), 100)


class TestEventBusAsync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bus._event_history.clear()
        bus._subscribers.clear()
        bus._async_subs.clear()
        bus._filters.clear()

    async def test_emit_await_async_subscriber(self):
        results = []

        async def async_cb(payload):
            await asyncio.sleep(0.01)
            results.append(payload)

        bus.subscribe_async("async.event", async_cb)
        await bus.emit_await("async.event", {"key": "val"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("key"), "val")

    async def test_emit_async_background_fire_and_forget(self):
        results = []

        async def slow_cb(payload):
            await asyncio.sleep(0.1)
            results.append(payload)

        bus.subscribe_async("bg.event", slow_cb)
        bus.emit("bg.event", {"n": 1})
        # Не ждём — в фоне
        self.assertEqual(len(results), 0)
        # Даём время
        await asyncio.sleep(0.2)
        self.assertEqual(len(results), 1)

    async def test_emit_await_all_subscribers_sync_and_async(self):
        sync_results = []
        async_results = []

        def sync_cb(p):
            sync_results.append(p)

        async def async_cb(p):
            await asyncio.sleep(0.01)
            async_results.append(p)

        bus.subscribe("mix.event", sync_cb)
        bus.subscribe_async("mix.event", async_cb)
        await bus.emit_await("mix.event", {"x": 1})
        self.assertEqual(len(sync_results), 1)
        self.assertEqual(len(async_results), 1)

    async def test_emit_async_filter_respected(self):
        results = []

        async def cb(p):
            results.append(p)

        bus.subscribe_async("filtered.async", cb)
        bus.add_filter("filtered.async", lambda p: False)
        await bus.emit_await("filtered.async", {})
        self.assertEqual(len(results), 0)

    async def test_emit_await_wildcard_async(self):
        results = []

        async def cb(p):
            results.append(p.get("event_type"))

        bus.subscribe_async("*", cb)
        await bus.emit_await("star.event", {})
        self.assertEqual(results, ["star.event"])


if __name__ == "__main__":
    unittest.main()
