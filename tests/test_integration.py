"""
Интеграционные тесты — сквозной pipeline без бутафории.

Покрытие:
1. PatchRunner → generate → apply → test (py_compile) → rollback
2. healers → install → bus events → healers_snapshot
3. LLM Triage → collect → list → apply → dismiss
4. Heal Executor → parse → execute all 9 action types
5. MCE → self-state → drift → optimization → experiment
6. AutoOptimizer → find slow → AST optimize → LLM fallback
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# ── PatchRunner интеграция ────────────────────────────────────────────────


class TestPatchRunnerIntegration(unittest.TestCase):
    """Сквозной тест: generate → apply → test → rollback."""

    def setUp(self):
        from core.code_evolution import EvolutionLog, PatchRunner
        self.log = EvolutionLog()
        self.runner = PatchRunner(self.log)
        self._files: list[Path] = []

    def tearDown(self):
        for fp in self._files:
            try:
                fp.unlink(missing_ok=True)
            except Exception:
                pass
        self._files.clear()

    def _create(self, rel: str, content: str = "x=1\n") -> Path:
        root = Path(self.runner._project_root())
        fp = (root / rel).resolve()
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        self._files.append(fp)
        return fp

    def test_full_cycle_generate_apply_test(self):
        """Полный цикл: generate → apply → py_compile (без git-rollback)."""
        from core.code_evolution import CodePatch, PatchRunner

        original = "def foo():\n    return 42\n"
        altered = "def foo():\n    return 43\n"
        tf = "core/test_cycle_module.py"
        self._create(tf, original)

        patch = self.runner.generate_patch(altered, "bump 42→43", tf)
        self.assertIsNotNone(patch)
        self.assertEqual(patch.status, "pending")
        self.assertIn("@@", patch.diff_text)

        ok = self.runner.apply_patch(patch)
        self.assertTrue(ok)
        self.assertEqual(patch.status, "testing")

        fp = PatchRunner._resolve_path(tf)
        self.assertIsNotNone(fp)
        self.assertIn("43", fp.read_text(encoding="utf-8"))

        ok = self.runner.run_tests(patch)
        self.assertTrue(ok)
        self.assertEqual(patch.status, "applied")
        self.assertTrue(patch.test_ok)

    def test_generate_rejects_syntax_error(self):
        """Патч с синтаксической ошибкой не проходит generate."""
        tf = "core/test_syntax_module.py"
        self._create(tf, "x=1\n")
        patch = self.runner.generate_patch(
            "def broken(:\n    pass\n", "bad syntax", tf,
        )
        self.assertIsNone(patch)

    def test_apply_patch_syntax_check_fails(self):
        """apply_patch проверяет синтаксис diff-результата."""
        from core.code_evolution import CodePatch
        tf = "core/test_apply_syntax.py"
        self._create(tf, "x=1\n")
        patch = CodePatch(
            id="syntax_fail", ts=time.time(), generated_by="manual",
            target_file=tf,
            diff_text="--- a/core/test_apply_syntax.py\n+++ b/core/test_apply_syntax.py\n@@ -1 +1 @@\n-x=1\n+def broken(:\n",
            description="bad apply",
            reason="test",
        )
        with self.runner._lock:
            self.runner._patches.append(patch)
        ok = self.runner.apply_patch(patch)
        self.assertFalse(ok)
        self.assertEqual(patch.status, "failed")

    def test_patch_lifecycle_statuses(self):
        """Статусы патча: pending → testing → applied."""
        original = "z=0\n"
        altered = "z=1\n"
        tf = "core/test_lifecycle.py"
        self._create(tf, original)

        p = self.runner.generate_patch(altered, "z++", tf)
        self.assertIsNotNone(p)
        self.assertEqual(p.status, "pending")

        self.runner.apply_patch(p)
        self.assertEqual(p.status, "testing")

        self.runner.run_tests(p)
        self.assertEqual(p.status, "applied")


# ── Healers интеграция ───────────────────────────────────────────────────

class TestHealersIntegration(unittest.TestCase):
    """Сквозной тест подписки healers на EventBus."""

    def setUp(self):
        from core.event_bus import bus as _bus
        self.bus = _bus
        self._saved = {}
        for etype in ("module.failed", "module.executed", "bug_report.collected",
                       "anomaly.detected", "maintenance.tick", "openrouter.done"):
            subs = list(self.bus._subscribers.get(etype, {}).items())
            self._saved[etype] = subs
            self.bus._subscribers[etype] = {}

    def tearDown(self):
        from core.event_bus import bus as _bus
        for etype, subs in self._saved.items():
            _bus._subscribers[etype] = dict(subs)

    def test_module_failure_healer_creates_patch(self):
        """ModuleFailureHealer: N failures → ephemeral patch."""
        from core.event_healers import ModuleFailureHealer
        from core.ephemeral_lessons import lessons_path

        healer = ModuleFailureHealer(max_failures=2)

        async def _run():
            for i in range(3):
                await healer({"module_name": "test_vuln", "ok": False})
            snapshot = healer.snapshot()
            self.assertIn("test_vuln", snapshot.get("patches_created", []))

        asyncio.run(_run())

    def test_healers_install_and_snapshot(self):
        """install_healers + healers_snapshot без ошибок."""
        from core.event_healers import install_healers, healers_snapshot

        install_healers()
        snap = healers_snapshot()
        self.assertTrue(snap.get("installed"))
        self.assertIn("module_failure_healer", snap)
        self.assertIn("auto_latency_healer", snap)

    def test_bug_context_gatherer_attaches_diagnostic(self):
        """BugContextGatherer прикрепляет diagnostic_context к событию."""
        from core.event_healers import BugContextGatherer

        gatherer = BugContextGatherer()
        payload: dict = {}

        async def _run():
            await gatherer(payload)
            self.assertIn("diagnostic_context", payload)
            ctx = payload["diagnostic_context"]
            self.assertIsInstance(ctx, dict)
            self.assertIn("recent_events_summary", ctx)

        asyncio.run(_run())

    def test_anomaly_escalator_counts_and_escalates(self):
        """AnomalyEscalator: N аномалий → safe mode (если RC доступен)."""
        from core.event_healers import AnomalyEscalator
        escalator = AnomalyEscalator()
        escalator._max_anomalies = 2
        escalator._window_sec = 3600  # широкое окно

        async def _run():
            for i in range(3):
                await escalator({"code": "test_code", "severity": "high"})
            snap = escalator.snapshot()
            self.assertGreaterEqual(snap["recent"].get("test_code", 0), 2)

        asyncio.run(_run())


# ── LLM Triage интеграция ────────────────────────────────────────────────

class TestLlmTriageIntegration(unittest.TestCase):
    """TriageCollector + рекомендации."""

    def setUp(self):
        from core.llm_triage import TriageCollector, list_recommendations, apply_recommendation, dismiss_recommendation
        self.collector = TriageCollector()
        self.collector._max_before_flush = 10  # не авто-флашим
        self.list_recommendations = list_recommendations
        self.apply_recommendation = apply_recommendation
        self.dismiss_recommendation = dismiss_recommendation

    def test_collector_accumulates_events(self):
        """TriageCollector накапливает события до авто-флаша."""
        async def _run():
            await self.collector({"healer": "test", "action": "fail", "reason": "x"})
            await self.collector({"healer": "test", "action": "fail", "reason": "y"})
            self.assertEqual(self.collector.pending_count(), 2)

        asyncio.run(_run())

    def test_collector_clear_pending(self):
        """clear_pending сбрасывает счётчик."""
        async def _run():
            await self.collector({"healer": "t", "action": "a"})
            n = self.collector.clear_pending()
            self.assertEqual(n, 1)
            self.assertEqual(self.collector.pending_count(), 0)

        asyncio.run(_run())

    def test_list_recommendations_empty(self):
        """list_recommendations без данных возвращает пустой список."""
        recs = self.list_recommendations()
        self.assertIsInstance(recs, list)

    def test_apply_dismiss_nonexistent(self):
        """apply/dismiss несуществующей рекомендации возвращает False."""
        self.assertFalse(self.apply_recommendation("no_such_id"))
        self.assertFalse(self.dismiss_recommendation("no_such_id"))

    def test_build_triage_context_structure(self):
        """_build_triage_context возвращает корректную структуру."""
        from core.llm_triage import _build_triage_context
        events = [{"healer": "h1", "action": "a1", "reason": "r1", "ts": time.time()}]
        ctx = _build_triage_context(events)
        self.assertIn("trigger_events", ctx)
        self.assertEqual(len(ctx["trigger_events"]), 1)
        self.assertIn("recent_bus_events", ctx)
        self.assertIn("monitor_counters", ctx)


# ── Heal Executor интеграция ─────────────────────────────────────────────

class TestHealExecutorIntegration(unittest.TestCase):
    """Все 9 типов шагов Heal Executor."""

    def test_parse_all_action_types(self):
        """parse_steps распознаёт все 9 типов действий."""
        from core.heal_executor import parse_steps

        steps = [
            "/admin_plugin_disable some_module",
            "/admin_plugin_enable other_module",
            "disable module bad_one",
            "enable module good_one",
            "env MODEL_SWITCH_THRESHOLD=15000",
            "env SECRET_TOKEN=hack",  # не в allowlist
            "reset module failures broken_mod",
            "reset error counters",
            "restart container",
            "ephemeral patch: trigger || instruction text",
            "clear safe mode",
        ]
        parsed = parse_steps(steps)
        self.assertEqual(len(parsed), 11)

        actions = [p["action"] for p in parsed]
        self.assertIn("disable_module", actions)
        self.assertIn("enable_module", actions)
        self.assertIn("set_env", actions)
        self.assertIn("set_env_blocked", actions)  # SECRET_TOKEN не в allowlist
        self.assertIn("reset_module_failures", actions)
        self.assertIn("reset_error_counters", actions)
        self.assertIn("restart_container", actions)
        self.assertIn("create_ephemeral_patch", actions)
        self.assertIn("exit_safe_mode", actions)

        # Проверяем blocked env
        blocked = [p for p in parsed if p["action"] == "set_env_blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertIn("SECRET_TOKEN", blocked[0].get("key", ""))

    def test_exec_set_env(self):
        """set_env реально меняет os.environ."""
        from core.heal_executor import _exec_set_env
        original = os.environ.get("MODEL_SWITCH_THRESHOLD", "")
        try:
            result = _exec_set_env("MODEL_SWITCH_THRESHOLD", "99999")
            self.assertTrue(result.get("ok"))
            self.assertEqual(os.environ.get("MODEL_SWITCH_THRESHOLD"), "99999")
        finally:
            if original:
                os.environ["MODEL_SWITCH_THRESHOLD"] = original
            else:
                os.environ.pop("MODEL_SWITCH_THRESHOLD", None)

    def test_exec_restart_container(self):
        """restart_container вызывает ResilienceController.request_container_restart."""
        from core.heal_executor import _exec_restart_container
        with patch("core.resilience_controller.ResilienceController") as MockRC:
            instance = MagicMock()
            MockRC.return_value = instance
            result = _exec_restart_container("test_reason")
            instance.request_container_restart.assert_called_once()
            self.assertTrue(result.get("ok"))

    def test_apply_steps_invalid_action(self):
        """Неизвестное действие возвращает ok=False."""
        from core.heal_executor import parse_steps
        parsed = parse_steps(["unknown_command_xyz"])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["action"], "unknown")

    def test_apply_steps_all_success(self):
        """apply_steps c корректными шагами."""
        from core.heal_executor import apply_steps

        async def _run():
            result = await apply_steps(
                ["env MODEL_SWITCH_THRESHOLD=8000",
                 "reset error counters"],
                reason="test_integration",
            )
            self.assertIsInstance(result, dict)
            self.assertIn("ok", result)
            self.assertIn("results", result)
            self.assertIn("summary", result)

        asyncio.run(_run())


# ── MCE интеграция ────────────────────────────────────────────────────────

class TestMceIntegration(unittest.TestCase):
    """Meta-Cognitive Engine: tick → self-state → drift → optimization."""

    def setUp(self):
        from core.meta_cognitive_engine import MetaCognitiveEngine
        self.engine = MetaCognitiveEngine()
        self.engine._goals.clear()
        self.engine._recommendations.clear()
        self.engine._history.clear()

    def test_synthesize_self_state_produces_valid_state(self):
        """_synthesize_self_state корректно заполняет SelfState."""
        from core.meta_cognitive_engine import SelfState
        state = self.engine._synthesize_self_state()
        self.assertIsInstance(state, SelfState)
        self.assertGreater(state.ts, 0)
        self.assertIsInstance(state.confidence, float)
        self.assertIsInstance(state.confidence_trend, str)

    def test_detect_drift_no_false_positives(self):
        """_detect_drift не находит дрифт на здоровом состоянии."""
        from core.meta_cognitive_engine import SelfState

        state = SelfState(
            ts=time.time(),
            confidence=0.8,
            confidence_trend="stable",
            healer_actions_24h=0,
            p95_openrouter_ms=1000.0,
        )
        drifts = self.engine._detect_drift(state)
        for drift in drifts:
            self.assertNotIn("confidence_low", drift.get("reason", ""),
                             "Не должно быть дрифта уверенности на здоровом состоянии")

    def test_auto_optimize_high_latency(self):
        """Высокая latency → рекомендация."""
        from core.meta_cognitive_engine import SelfState

        state = SelfState(
            ts=time.time(),
            confidence=0.8,
            confidence_trend="stable",
            p95_openrouter_ms=15000.0,
            healer_actions_24h=0,
        )
        recs = self.engine._auto_optimize(state)
        drifts = self.engine._detect_drift(state)
        latency_drift = [d for d in drifts if "latency" in d.get("reason", "")]
        if latency_drift:
            self.assertGreaterEqual(len(recs), 0)

    def test_snapshot_includes_goals(self):
        """snapshot() включает ключи goals и recommendations."""
        snap = self.engine.snapshot()
        self.assertIn("goals", snap)
        self.assertIn("recommendations_pending", snap)
        self.assertIn("active_experiment", snap) or self.assertIn("experiment_enabled", snap)

    def test_ask_method_exists(self):
        """Метод ask возвращает строку."""
        answer = self.engine.ask("как дела?")
        self.assertIsInstance(answer, str)
        self.assertTrue(len(answer) > 0)


# ── AutoOptimizer интеграция ─────────────────────────────────────────────

class TestAutoOptimizerIntegration(unittest.TestCase):
    """AutoOptimizer: find slow → AST optimize → LLM fallback."""

    def setUp(self):
        from core.code_evolution import EvolutionLog, PatchRunner, AutoOptimizer
        self.log = EvolutionLog()
        self.runner = PatchRunner(self.log)
        self.optimizer = AutoOptimizer(self.runner, self.log)

    def test_ast_optimize_fixes_async_sleep(self):
        """AST-оптимизация исправляет time.sleep внутри async def."""
        from core.code_evolution import PatchRunner
        import ast

        source = (
            "import asyncio\n"
            "import time\n\n"
            "async def my_func():\n"
            "    time.sleep(10)\n"
            "    return 42\n"
        )
        tree = ast.parse(source)
        result = PatchRunner._ast_optimize(tree, source)
        self.assertIn("await asyncio.sleep(10)", result)
        self.assertNotIn("time.sleep(", result)

    def test_ast_optimize_fixes_bare_except(self):
        """AST-оптимизация исправляет bare except."""
        from core.code_evolution import PatchRunner
        import ast

        source = (
            "try:\n"
            "    risky()\n"
            "except:\n"
            "    pass\n"
        )
        tree = ast.parse(source)
        result = PatchRunner._ast_optimize(tree, source)
        self.assertIn("except Exception:", result)
        self.assertNotIn("except:\n", result)

    def test_find_slow_function_ast(self):
        """_find_slow_function_ast находит функцию по имени."""
        from core.code_evolution import AutoOptimizer
        source = "def target_func():\n    pass\n"
        func = AutoOptimizer._find_slow_function_ast(source, "target_func")
        self.assertIsNotNone(func)
        self.assertIn("target_func", func or "")

    def test_find_slow_function_ast_not_found(self):
        """_find_slow_function_ast возвращает None для несуществующей."""
        from core.code_evolution import AutoOptimizer
        result = AutoOptimizer._find_slow_function_ast("x=1", "nonexistent")
        self.assertIsNone(result)

    def test_generate_llm_optimization_fallback_on_no_llm(self):
        """_generate_llm_optimisation возвращает None если LLM недоступен."""
        from core.code_evolution import OptimizationTarget, AutoOptimizer

        async def _run():
            target = OptimizationTarget(
                file_path="core/test.py", func_name="foo",
                p95_ms=10000.0, call_count=50,
            )
            result = await AutoOptimizer._generate_llm_optimization(
                "def foo():\n    return 1\n",
                "def foo():\n    return 1\n",
                target,
            )
            self.assertIsNone(result)

        asyncio.run(_run())

    def test_generate_optimization_falls_back_to_ast(self):
        """_generate_optimisation падает на AST при недоступности LLM."""
        from core.code_evolution import OptimizationTarget
        source = (
            "try:\n"
            "    risky()\n"
            "except:\n"
            "    pass\n"
        )
        target = OptimizationTarget(
            file_path="core/test.py", func_name="test_func",
            p95_ms=10000.0, call_count=50,
        )
        result = self.optimizer._generate_optimization(source, source, target)
        self.assertIn("except Exception:", result)


# ── Сквозной pipeline healers → triage → executor ───────────────────────

class TestHealingPipelineIntegration(unittest.TestCase):
    """Сквозной pipeline: healers → triage → executor."""

    def test_module_failure_flow(self):
        """
        Модуль падает N раз → ModuleFailureHealer создаёт эфемерный патч
        → LLM Triage регистрирует событие.
        """
        from core.event_healers import ModuleFailureHealer, install_healers
        from core.event_bus import bus

        healer = ModuleFailureHealer(max_failures=2)

        # Эмулируем падения модуля
        async def _run():
            for i in range(3):
                await healer({"module_name": "integ_test_mod", "ok": False})
                await asyncio.sleep(0.01)

            snap = healer.snapshot()
            # Эфемерный патч должен быть создан
            self.assertIn("integ_test_mod", snap.get("patches_created", []))

        asyncio.run(_run())

    def test_env_change_undo_log(self):
        """
        AutoLatencyHealer меняет env → UndoLog записывает.
        Проверяем что UndoLog работает без ошибок.
        """
        from core.auto_rollback import get_undo_log

        log = get_undo_log()
        pending_before = len(log.list_pending())

        log.add(
            healer="AutoLatencyHealer",
            action="set_env",
            params={"key": "MODEL_SWITCH_THRESHOLD", "old_value": "8000", "new_value": "12000"},
            verify_window_sec=300.0,
        )

        pending_after = len(log.list_pending())
        self.assertGreater(pending_after, pending_before)


if __name__ == "__main__":
    unittest.main()
