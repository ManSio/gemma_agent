"""Тесты LLM Triage — анализ healers через LLM."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.event_bus import bus
from core.llm_triage import (
    TriageCollector,
    _parse_triage_json,
    _should_skip_triage_event,
    install_triage,
    list_recommendations,
    get_recommendation,
    apply_recommendation,
    dismiss_recommendation,
    snapshot,
    get_collector,
)


class TestTriageJsonParse(unittest.TestCase):
    def test_parse_with_trailing_comma(self):
        raw = '{"analysis": "ok", "priority": "high", "steps": ["a"],}'
        obj = _parse_triage_json(raw)
        self.assertEqual(obj.get("priority"), "high")

    def test_parse_from_fence(self):
        raw = '```json\n{"analysis": "x", "priority": "low", "steps": []}\n```'
        obj = _parse_triage_json(raw)
        self.assertEqual(obj.get("analysis"), "x")

    def test_skip_mce_self_referential(self):
        self.assertTrue(
            _should_skip_triage_event(
                {"healer": "MetaCognitiveEngine", "action": "tighten_healer_thresholds"}
            )
        )
        self.assertTrue(
            _should_skip_triage_event(
                {"healer": "MetaCognitiveEngine", "action": "suggest_faster_model"}
            )
        )
        self.assertFalse(
            _should_skip_triage_event({"healer": "ModuleFailureHealer", "action": "patch"})
        )

    def test_run_triage_skips_only_mce_events(self):
        import asyncio
        from core.llm_triage import TriageCollector

        async def mock_llm(*_a, **_k):
            return {"analysis": "x", "priority": "low", "steps": []}

        with patch("core.llm_triage._call_llm_for_triage", side_effect=mock_llm):
            result = asyncio.run(
                TriageCollector._run_triage([
                    {"healer": "MetaCognitiveEngine", "action": "tighten_healer_thresholds"},
                ])
            )
        self.assertIsNone(result)


class TestTriageCollector(unittest.TestCase):
    def setUp(self):
        bus._event_history.clear()
        bus._subscribers.clear()
        bus._async_subs.clear()
        bus._filters.clear()
        # Сброс коллектора
        self.collector = TriageCollector()
        self.collector._max_before_flush = 10  # не срабатывает авто
        self.collector._pending_events.clear()

    def test_pending_count_empty(self):
        self.assertEqual(self.collector.pending_count(), 0)

    def test_accumulate_events(self):
        async def feed():
            await self.collector({
                "healer": "ModuleFailureHealer",
                "action": "create_ephemeral_patch",
                "reason": "test",
                "event_type": "healer.action",
            })

        import asyncio
        asyncio.run(feed())
        self.assertEqual(self.collector.pending_count(), 1)

        asyncio.run(feed())
        self.assertEqual(self.collector.pending_count(), 2)

    def test_clear_pending(self):
        async def feed():
            await self.collector({"healer": "test", "action": "x", "reason": "y",
                                   "event_type": "healer.action"})

        import asyncio
        asyncio.run(feed())
        asyncio.run(feed())
        self.assertEqual(self.collector.clear_pending(), 2)
        self.assertEqual(self.collector.pending_count(), 0)

    def test_list_no_recommendations(self):
        recs = list_recommendations()
        self.assertIsInstance(recs, list)

    def test_apply_dismiss_flow(self):
        # Добавляем тестовую рекомендацию напрямую в хранилище
        from core.llm_triage import _TRIAGE_STORE, _save_store
        test_rec = {
            "id": "test123",
            "ts": "2026-05-16T23:00:00",
            "events": [],
            "analysis": "test analysis",
            "steps": ["step 1"],
            "priority": "high",
            "status": "pending",
            "applied_at": None,
        }
        _TRIAGE_STORE.append(test_rec)

        self.assertIsNotNone(get_recommendation("test123"))

        self.assertTrue(apply_recommendation("test123"))
        rec = get_recommendation("test123")
        self.assertEqual(rec["status"], "applied")
        self.assertIsNotNone(rec["applied_at"])

        # Нельзя применить повторно
        self.assertFalse(apply_recommendation("test123"))

        # Очищаем
        _TRIAGE_STORE.clear()

    def test_dismiss(self):
        from core.llm_triage import _TRIAGE_STORE
        test_rec = {
            "id": "dismiss_me",
            "ts": "2026-05-16T23:00:00",
            "events": [],
            "analysis": "bad",
            "steps": [],
            "priority": "low",
            "status": "pending",
            "applied_at": None,
        }
        _TRIAGE_STORE.append(test_rec)

        self.assertTrue(dismiss_recommendation("dismiss_me"))
        rec = get_recommendation("dismiss_me")
        self.assertEqual(rec["status"], "dismissed")
        _TRIAGE_STORE.clear()

    def test_snapshot_structure(self):
        snap = snapshot()
        self.assertIn("enabled", snap)
        self.assertIn("installed", snap)
        self.assertIn("pending_events", snap)
        self.assertIn("recommendations", snap)

    def test_install_triage_idempotent(self):
        install_triage()
        cnt1 = bus.subscriber_count()
        install_triage()
        cnt2 = bus.subscriber_count()
        self.assertEqual(cnt1, cnt2)
        # Проверяем, что подписчик на healer.action есть
        subs = bus.subscriber_count()
        self.assertIn("healer.action", subs)


class TestTriageCollectorAutoFlush(unittest.TestCase):
    def setUp(self):
        bus._event_history.clear()
        bus._subscribers.clear()
        bus._async_subs.clear()
        bus._filters.clear()

    def test_autoflush_triggers_at_threshold(self):
        collector = TriageCollector()
        collector._max_before_flush = 2

        call_args_list = []

        async def mock_run(self_or_events, _events=None):
            # Когда просто назначено на класс — self передаётся первым
            if _events is not None:
                events = _events
            else:
                events = self_or_events
            call_args_list.append(list(events) if events else [])
            return "mock_id"

        import asyncio
        original = TriageCollector._run_triage
        try:
            TriageCollector._run_triage = mock_run
            for _ in range(2):
                asyncio.run(collector({
                    "healer": "test", "action": "x",
                    "reason": "auto",
                    "event_type": "healer.action",
                }))
        finally:
            TriageCollector._run_triage = original

        self.assertEqual(len(call_args_list), 1)
        self.assertEqual(len(call_args_list[0]), 2)

    def test_autoflush_does_not_trigger_below_threshold(self):
        collector = TriageCollector()
        collector._max_before_flush = 5

        call_args_list = []

        async def mock_run(self_or_events, _events=None):
            if _events is not None:
                events = _events
            else:
                events = self_or_events
            call_args_list.append(list(events) if events else [])
            return "mock_id"

        import asyncio
        original = TriageCollector._run_triage
        try:
            TriageCollector._run_triage = mock_run
            for _ in range(3):
                asyncio.run(collector({
                    "healer": "test", "action": "x",
                    "reason": "auto",
                    "event_type": "healer.action",
                }))
        finally:
            TriageCollector._run_triage = original

        self.assertEqual(len(call_args_list), 0)
