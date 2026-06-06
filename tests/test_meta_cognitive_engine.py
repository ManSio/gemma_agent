"""Тесты Meta-Cognitive Engine (MCE)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.meta_cognitive_engine import (
    count_healer_actions_24h,
    MetaCognitiveEngine,
    SelfState,
    Experiment,
    MceRecommendation,
    MceGoal,
    get_mce,
)


class TestHealerFloodCount(unittest.TestCase):
    def test_count_24h_excludes_mce_noise_and_old(self):
        now = time.time()
        old_ts = (now - 90000)  # > 24h
        recent_ts = now - 3600
        events = [
            MagicMock(data={
                "healer": "AutoLatencyHealer",
                "action": "set_env",
                "ts": recent_ts,
            }),
            MagicMock(data={
                "healer": "MetaCognitiveEngine",
                "action": "tighten_healer_thresholds",
                "ts": recent_ts,
            }),
            MagicMock(data={
                "healer": "ModuleFailureHealer",
                "action": "patch",
                "ts": old_ts,
            }),
        ]
        self.assertEqual(count_healer_actions_24h(events), 1)


class TestSelfState(unittest.TestCase):
    def test_default_values(self):
        s = SelfState()
        self.assertEqual(s.confidence, 0.5)
        self.assertEqual(s.confidence_trend, "stable")
        self.assertEqual(s.safe_mode, False)
        self.assertEqual(s.lesson_active_count, 0)

    def test_to_dict(self):
        s = SelfState(confidence=0.8, confidence_trend="up", healer_actions_24h=3)
        d = s.to_dict()
        self.assertEqual(d["confidence"], 0.8)
        self.assertEqual(d["confidence_trend"], "up")
        self.assertEqual(d["healer_actions_24h"], 3)


class TestMceRecommendation(unittest.TestCase):
    def test_defaults(self):
        r = MceRecommendation(
            id="test1", ts=100.0, reason="test", suggestion="suggest",
            param="P", old_value="a", new_value="b",
        )
        self.assertEqual(r.status, "pending")
        self.assertEqual(r.id, "test1")


class TestExperiment(unittest.TestCase):
    def test_defaults(self):
        e = Experiment(
            id="exp1", param="P", control_value="1", treatment_value="2",
            traffic_fraction=0.1, started_at=100.0, duration_cycles=50,
        )
        self.assertEqual(e.status, "running")


class TestMetaCognitiveEngine(unittest.TestCase):
    def setUp(self):
        self.engine = MetaCognitiveEngine()
        self.engine._enabled = True
        self.engine._tick_interval = 1  # каждый раз
        self.engine._goals.clear()

    def test_init_disabled(self):
        with patch.dict(os.environ, {"MCE_ENABLED": "false"}):
            e = MetaCognitiveEngine()
            self.assertFalse(e._enabled)

    def test_tick_skipped_when_disabled(self):
        self.engine._enabled = False
        with patch.object(self.engine, "_synthesize_self_state") as mock:
            asyncio_run(self.engine.tick())
            mock.assert_not_called()

    def test_tick_skipped_on_interval(self):
        self.engine._tick_interval = 5
        self.engine._tick_counter = 3
        with patch.object(self.engine, "_synthesize_self_state") as mock:
            asyncio_run(self.engine.tick())
            mock.assert_not_called()

    def test_tick_fires_on_interval(self):
        self.engine._tick_interval = 3
        self.engine._tick_counter = 2  # next tick = 3
        with patch.object(self.engine, "_synthesize_self_state", return_value=SelfState()):
            asyncio_run(self.engine.tick())
            self.assertEqual(self.engine._tick_counter, 3)
            self.assertIsNotNone(self.engine._last_synthesis)

    def test_synthesize_self_state_basic(self):
        """Проверить, что синтез не падает при недоступных модулях."""
        state = self.engine._synthesize_self_state()
        self.assertIsNotNone(state)
        self.assertGreaterEqual(state.confidence, 0.0)

    def test_detect_drift_no_drift(self):
        state = SelfState(
            confidence=0.7,
            confidence_trend="stable",
            lesson_active_count=5,
            lesson_avg_effectiveness=0.6,
            p95_openrouter_ms=5000,
            healer_actions_24h=2,
        )
        drifts = self.engine._detect_drift(state)
        self.assertEqual(len(drifts), 0)

    def test_detect_drift_lesson_low(self):
        state = SelfState(
            lesson_active_count=5,
            lesson_avg_effectiveness=0.2,
        )
        drifts = self.engine._detect_drift(state)
        self.assertTrue(any(d["reason"] == "lesson_effectiveness_low" for d in drifts))

    def test_detect_drift_confidence_low(self):
        state = SelfState(
            confidence=0.3,
            confidence_trend="down",
        )
        drifts = self.engine._detect_drift(state)
        self.assertTrue(any(d["reason"] == "confidence_dropping" for d in drifts))

    def test_detect_drift_latency_high(self):
        # Дефолт MCE_LATENCY_P95_DRIFT_MS=28000 — 20s не считается дрейфом (шум).
        state = SelfState(p95_openrouter_ms=35000)
        drifts = self.engine._detect_drift(state)
        self.assertTrue(any(d["reason"] == "openrouter_latency_high" for d in drifts))

    def test_detect_drift_healer_flood(self):
        state = SelfState(healer_actions_24h=35)
        drifts = self.engine._detect_drift(state)
        self.assertTrue(any(d["reason"] == "healer_action_flood" for d in drifts))

    def test_auto_optimize_noop(self):
        state = SelfState(confidence=0.5, confidence_trend="stable")
        opts = self.engine._auto_optimize(state)
        self.assertEqual(len(opts), 0)

    def test_auto_optimize_confidence_high(self):
        state = SelfState(confidence=0.9, confidence_trend="up")
        opts = self.engine._auto_optimize(state)
        self.assertTrue(any(o.param == "META_INTENT_MIN_CONFIDENCE" for o in opts))

    def test_auto_optimize_healer_flood(self):
        state = SelfState(healer_actions_24h=35)
        opts = self.engine._auto_optimize(state)
        self.assertTrue(any(o.param == "RESILIENCE_AUTONOMY_ENABLED" for o in opts))

    def test_auto_optimize_latency_high(self):
        with patch.dict(os.environ, {"DEFAULT_LLM_MODEL": "openai/gpt-4o"}):
            state = SelfState(p95_openrouter_ms=46000)
            opts = self.engine._auto_optimize(state)
            self.assertTrue(any(o.param == "DEFAULT_LLM_MODEL" for o in opts))

    def test_write_and_list_recommendations(self):
        rec = MceRecommendation(
            id="rec1", ts=time.time(), reason="test", suggestion="test",
            param="P", old_value="a", new_value="b",
        )
        self.engine._write_recommendation(rec)
        recs = self.engine.list_recommendations()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["id"], "rec1")

    def test_snapshot_structure(self):
        snap = self.engine.snapshot()
        self.assertIn("enabled", snap)
        self.assertIn("tick_counter", snap)
        self.assertIn("self_state", snap)
        self.assertIn("active_experiment", snap)
        self.assertIn("recommendations_pending", snap)

    def test_experiment_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"RESILIENCE_RUNTIME_DIR": tmp}):
                e = MetaCognitiveEngine()
                e._experiment = Experiment(
                    id="exp_test", param="P", control_value="1", treatment_value="2",
                    traffic_fraction=0.1, started_at=time.time(), duration_cycles=50,
                )
                e._save_experiment()
                # Проверить, что файл создан
                exp_path = Path(tmp) / "mce_experiment.json"
                self.assertTrue(exp_path.exists())
                data = json.loads(exp_path.read_text(encoding="utf-8"))
                self.assertEqual(data["id"], "exp_test")

    def test_rec_path(self):
        path = self.engine._rec_path()
        self.assertTrue(str(path).endswith("mce_recommendations.jsonl"))

    def test_get_mce_singleton(self):
        mce1 = get_mce()
        mce2 = get_mce()
        self.assertIs(mce1, mce2)

    def test_to_triage_style(self):
        rec = MceRecommendation(
            id="mce1", ts=100.0, reason="test", suggestion="boost autonomy",
            param="P", old_value="a", new_value="b",
        )
        ts = rec.to_triage_style()
        self.assertEqual(ts["id"], "mce1")
        self.assertEqual(ts["source"], "mce")
        self.assertIn("[MCE]", ts["analysis"])
        self.assertEqual(ts["param"], "P")

    def test_record_recommendation_outcome(self):
        rec = MceRecommendation(
            id="rec_x", ts=time.time(), reason="test", suggestion="test",
            param="P", old_value="a", new_value="b",
        )
        self.engine._write_recommendation(rec)
        self.engine.record_recommendation_outcome("rec_x", "applied")
        with self.engine._lock:
            self.assertEqual(self.engine._recommendations[0].status, "applied")

        hist = self.engine.get_history(limit=5)
        self.assertTrue(any(
            h["event_type"] == "recommendation_applied" and h["details"].get("rec_id") == "rec_x"
            for h in hist
        ))

    def test_meta_learning_loop_threshold_adjust(self):
        """Meta-Learning: если dismissed > applied → ослабить пороги."""
        engine = MetaCognitiveEngine()
        # Добавить 5 dismissed и 2 applied
        for _ in range(5):
            engine._add_history("recommendation_dismissed", {"rec_id": "x"})
        for _ in range(2):
            engine._add_history("recommendation_applied", {"rec_id": "y"})
        old_lesson = engine._dynamic_thresholds.get("lesson_eff_low", 0.4)
        old_flood = engine._dynamic_thresholds.get("healer_flood", 10.0)

        engine._meta_learning_loop()

        # Порог должен уменьшиться (ослабиться)
        self.assertLess(engine._dynamic_thresholds.get("lesson_eff_low", 0.5), old_lesson)
        self.assertGreater(engine._dynamic_thresholds.get("healer_flood", 0), old_flood)

    def test_meta_learning_loop_too_few_records(self):
        """Ничего не менять, если < 3 записей."""
        engine = MetaCognitiveEngine()
        engine._add_history("recommendation_applied", {"rec_id": "a"})
        old = dict(engine._dynamic_thresholds)
        engine._meta_learning_loop()
        self.assertEqual(engine._dynamic_thresholds, old)

    def test_auto_optimize_lesson_deprecated(self):
        state = SelfState(lesson_active_count=5, lesson_avg_effectiveness=0.05)
        opts = self.engine._auto_optimize(state)
        self.assertTrue(any(o.param == "INTERNAL_LESSON_CLEANUP" for o in opts))

    def test_snapshot_includes_dynamic_thresholds_and_history(self):
        self.engine._add_history("threshold_adjusted", {"test": True})
        snap = self.engine.snapshot()
        self.assertIn("dynamic_thresholds", snap)
        self.assertIn("history_recent", snap)
        self.assertIn("history_total", snap)
        self.assertEqual(snap["history_total"], 1)

    def test_add_history(self):
        self.engine._add_history("test_event", {"key": "val"})
        hist = self.engine.get_history(limit=10)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["event_type"], "test_event")
        self.assertEqual(hist[0]["details"]["key"], "val")

    # ─── Phase 8.1: Digest ──────────────────────────────────────────────

    def test_build_digest_no_synthesis(self):
        text = self.engine.build_digest()
        self.assertIn("ни одного синтеза", text.lower())

    def test_build_digest_with_state(self):
        self.engine._last_synthesis = SelfState(
            confidence=0.7,
            confidence_trend="stable",
            lesson_active_count=5,
            lesson_avg_effectiveness=0.6,
            p95_openrouter_ms=5000,
            p95_telegram_ms=200,
            healer_actions_24h=0,
            route_risk_active=1,
            experience_hit_rate_100=0.8,
        )
        text = self.engine.build_digest()
        self.assertIn("Уверенность", text)
        self.assertIn("Уроки", text)
        self.assertIn("p95", text)
        self.assertIn("hit-rate", text.lower() or "hit rate" in text.lower())

    def test_build_digest_mentions_drifts(self):
        self.engine._last_synthesis = SelfState(
            p95_openrouter_ms=20000,
            lesson_active_count=5,
            lesson_avg_effectiveness=0.2,
        )
        text = self.engine.build_digest()
        self.assertIn("дрейф", text.lower())

    # ─── Phase 8.2: Ask ─────────────────────────────────────────────────

    def test_ask_no_state(self):
        answer = self.engine.ask("как дела?")
        self.assertIn("ещё не выполнил", answer.lower())

    def test_ask_confidence(self):
        self.engine._last_synthesis = SelfState(confidence=0.75, confidence_trend="up")
        answer = self.engine.ask("какая уверенность?")
        self.assertIn("75%", answer)
        self.assertIn("up", answer)

    def test_ask_latency(self):
        self.engine._last_synthesis = SelfState(p95_openrouter_ms=12000, p95_telegram_ms=300)
        answer = self.engine.ask("латентность")
        self.assertIn("12000", answer)
        self.assertIn("p95", answer)

    def test_ask_lessons(self):
        self.engine._last_synthesis = SelfState(lesson_active_count=7, lesson_total_count=10, lesson_avg_effectiveness=0.4)
        answer = self.engine.ask("уроки")
        self.assertIn("7", answer)
        self.assertIn("40", answer)

    def test_ask_experiments(self):
        self.engine._experiment = Experiment(
            id="exp_q", param="P", control_value="1", treatment_value="2",
            traffic_fraction=0.1, started_at=time.time(), duration_cycles=50,
        )
        self.engine._last_synthesis = SelfState()
        answer = self.engine.ask("эксперимент")
        self.assertIn("Статус", answer)
        self.assertIn("running", answer)

    def test_ask_drifts(self):
        self.engine._last_synthesis = SelfState(p95_openrouter_ms=35000)
        answer = self.engine.ask("дрейфы")
        self.assertIn("openrouter_latency_high", answer.lower())

    def test_ask_recommendations(self):
        self.engine._recommendations.clear()
        self.engine._last_synthesis = SelfState(healer_actions_24h=11)
        answer = self.engine.ask("рекомендации")
        self.assertIn("рекомендац", answer.lower())

    def test_ask_goals(self):
        # Очистить цели — может быть spillover от предыдущих тестов синглтона
        self.engine._goals.clear()
        self.engine._last_synthesis = SelfState()
        answer = self.engine.ask("цели")
        # Ответ должен содержать информацию о целях (активных или "нет")
        self.assertTrue("цели" in answer.lower() or "нет" in answer.lower() or "active" in answer.lower())

    def test_ask_goals_active(self):
        self.engine._goals.append(MceGoal(
            id="g1", description="Test goal", metric="p95_openrouter_ms",
            target_value=5000, baseline_value=10000, created_at=time.time(),
            deadline_cycles=100, progress_pct=50,
        ))
        self.engine._last_synthesis = SelfState()
        answer = self.engine.ask("цели")
        self.assertIn("Test goal", answer)

    def test_ask_history(self):
        self.engine._add_history("threshold_adjusted", {"test": True})
        self.engine._last_synthesis = SelfState()
        answer = self.engine.ask("история")
        self.assertIn("threshold_adjusted", answer)

    def test_ask_fallback(self):
        self.engine._last_synthesis = SelfState(confidence=0.6, lesson_active_count=3, p95_openrouter_ms=5000)
        answer = self.engine.ask("что-то непонятное")
        self.assertIn("60%", answer)

    # ─── Phase 8.3: Self-Goals ──────────────────────────────────────────

    def test_set_goals_from_drifts_p95(self):
        self.engine._last_synthesis = SelfState(p95_openrouter_ms=15000)
        self.engine._set_goals_from_drifts()
        self.assertTrue(any(g.metric == "p95_openrouter_ms" for g in self.engine._goals))

    def test_set_goals_from_drifts_lessons(self):
        self.engine._last_synthesis = SelfState(
            lesson_active_count=5,
            lesson_avg_effectiveness=0.3,
        )
        self.engine._set_goals_from_drifts()
        self.assertTrue(any(g.metric == "lesson_avg_effectiveness" for g in self.engine._goals))

    def test_set_goals_from_drifts_healer(self):
        self.engine._last_synthesis = SelfState(healer_actions_24h=10)
        self.engine._set_goals_from_drifts()
        self.assertTrue(any(g.metric == "healer_actions_24h" for g in self.engine._goals))

    def test_set_goals_from_drifts_confidence(self):
        self.engine._last_synthesis = SelfState(confidence=0.4)
        self.engine._set_goals_from_drifts()
        self.assertTrue(any(g.metric == "confidence" for g in self.engine._goals))

    def test_goal_persistence(self):
        self.engine._goals.append(MceGoal(
            id="g_persist", description="persist test", metric="p95_openrouter_ms",
            target_value=5000, baseline_value=10000, created_at=time.time(),
            deadline_cycles=100,
        ))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"RESILIENCE_RUNTIME_DIR": tmp}):
                self.engine._save_goals()
                # Загрузить новый engine
                e2 = MetaCognitiveEngine()
                self.assertTrue(any(g.id == "g_persist" for g in e2._goals))

    def test_track_goals_achieved(self):
        self.engine._last_synthesis = SelfState(p95_openrouter_ms=4000)
        self.engine._goals.append(MceGoal(
            id="g_achieve", description="achieve test", metric="p95_openrouter_ms",
            target_value=5000, baseline_value=15000, created_at=time.time() - 100,
            deadline_cycles=1000,
        ))
        self.engine._track_goals()
        g = next(g for g in self.engine._goals if g.id == "g_achieve")
        self.assertEqual(g.status, "achieved")
        self.assertEqual(g.progress_pct, 100.0)

    def test_track_goals_progress_partial(self):
        self.engine._last_synthesis = SelfState(p95_openrouter_ms=10000)
        self.engine._goals.append(MceGoal(
            id="g_prog", description="progress test", metric="p95_openrouter_ms",
            target_value=5000, baseline_value=15000, created_at=time.time(),
            deadline_cycles=1000,
        ))
        self.engine._track_goals()
        g = next(g for g in self.engine._goals if g.id == "g_prog")
        self.assertGreater(g.progress_pct, 0)
        self.assertLess(g.progress_pct, 100)

    def test_track_goals_abandoned(self):
        self.engine._last_synthesis = SelfState(p95_openrouter_ms=15000)
        self.engine._goals.append(MceGoal(
            id="g_aban", description="abandon test", metric="p95_openrouter_ms",
            target_value=5000, baseline_value=15000, created_at=time.time() - 100000,
            deadline_cycles=1,
        ))
        self.engine._track_goals()
        g = next(g for g in self.engine._goals if g.id == "g_aban")
        self.assertEqual(g.status, "abandoned")

    def test_list_goals(self):
        self.engine._goals.append(MceGoal(
            id="g_list", description="list test", metric="p95_openrouter_ms",
            target_value=5000, baseline_value=10000, created_at=time.time(),
            deadline_cycles=100,
        ))
        goals = self.engine.list_goals()
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["id"], "g_list")

    def test_snapshot_includes_goals(self):
        snap = self.engine.snapshot()
        self.assertIn("goals", snap)
        self.assertIn("last_digest_ts", snap)

    def test_digest_due_default(self):
        self.engine._last_digest_ts = time.time()
        self.assertFalse(self.engine._digest_due())

    def test_digest_due_true(self):
        self.engine._last_digest_ts = 0.0
        self.assertTrue(self.engine._digest_due())

    def test_digest_due_after_interval(self):
        with patch.dict(os.environ, {"MCE_DIGEST_INTERVAL_HOURS": "0"}):
            self.assertFalse(self.engine._digest_due())


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
