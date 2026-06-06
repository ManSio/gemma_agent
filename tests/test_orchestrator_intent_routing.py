import os
import tempfile
import unittest
from unittest.mock import patch

from core.orchestrator import Orchestrator
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine


class OrchestratorIntentRoutingTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        pr = PluginRegistry(self._td.name)
        pe = PolicyEngine()
        self.o = Orchestrator(plugin_registry=pr, policy_engine=pe)

    def test_detect_intent_prefers_test_for_series(self):
        self.assertEqual(self.o._detect_intent("Проведи F-series regression test"), "test")
        self.assertEqual(self.o._detect_intent("Это S-series тест"), "test")

    def test_detect_intent_prefers_reasoning_for_delta_logic(self):
        self.assertEqual(self.o._detect_intent("Разбери δ-игру и логику переходов"), "reasoning")

    def test_detect_intent_prefers_explain_when_requested(self):
        self.assertEqual(self.o._detect_intent("Объясни почему ответ нестабилен"), "explain")

    def test_detect_intent_prefers_code_for_fix_requests(self):
        self.assertEqual(self.o._detect_intent("Пофикси баг в роутере intent"), "code")

    def test_detect_intent_keeps_math_for_explicit_math(self):
        self.assertEqual(self.o._detect_intent("2+2"), "math")

    def test_detect_intent_locks_test_mode_on_short_continuation(self):
        persisted = {"dialogue_state": {"last_intent": "test"}}
        self.assertEqual(self.o._detect_intent("дальше", persisted), "test")

    def test_detect_intent_locks_reasoning_mode_on_short_continuation(self):
        persisted = {"dialogue_state": {"last_intent": "reasoning"}}
        self.assertEqual(self.o._detect_intent("continue", persisted), "reasoning")

    def test_detect_intent_allows_explicit_mode_switch(self):
        persisted = {"dialogue_state": {"last_intent": "test"}}
        self.assertEqual(self.o._detect_intent("переключи режим на reasoning", persisted), "reasoning")

    def test_detect_intent_stress_test_reasoning_text_not_forced_to_test(self):
        txt = (
            "Это редкий reasoning-кейс под полной неопределённостью. "
            "Хочу стресс-тест на устойчивость вывода, но сейчас именно разбор логики."
        )
        self.assertEqual(self.o._detect_intent(txt), "reasoning")

    def test_detect_intent_structured_reasoning_question_not_explain(self):
        txt = (
            "Представь, что топология меняется без наблюдаемого правила, "
            "история траектории влияет на окно выхода, а наблюдения нестабильны. "
            "Вопрос: можно ли вообще говорить о рациональной стратегии в такой среде? "
            "Если да — опиши её концептуально, если нет — объясни, почему сама постановка "
            "разрушает понятие стратегии. Не добавляй скрытые правила."
        )
        self.assertEqual(self.o._detect_intent(txt), "reasoning")

    def test_detect_intent_dialog_recall_nl_when_enabled(self):
        with patch.dict(os.environ, {"DIALOG_RECALL_NL_ROUTE_ENABLED": "true"}, clear=False):
            self.assertEqual(self.o._detect_intent("Напомни, что мы обсуждали вчера"), "dialog_recall")

    def test_detect_intent_dialog_recall_nl_off_by_default(self):
        with patch.dict(os.environ, {"DIALOG_RECALL_NL_ROUTE_ENABLED": "false"}, clear=False):
            self.assertEqual(self.o._detect_intent("Напомни, что мы обсуждали вчера"), "general")

    def test_select_module_prefers_chat_orchestrator_for_explain(self):
        class _Manifest:
            def __init__(self, caps):
                self.capabilities = caps

        class _Mod:
            def __init__(self, caps):
                self.manifest = _Manifest(caps)

        self.o.plugin_registry.loaded_modules = {
            "school_assistant": _Mod(["explain", "teacher"]),
            "chat-orchestrator": _Mod(["general", "explain", "reasoning"]),
            "smartchat": _Mod(["general", "explain"]),
        }
        selected = self.o._select_module("explain", {"school_assistant", "chat-orchestrator", "smartchat"})
        self.assertEqual(selected, "chat-orchestrator")

    def test_select_module_dialog_recall_prefers_plugin(self):
        class _Manifest:
            def __init__(self, caps):
                self.capabilities = caps

        class _Mod:
            def __init__(self, caps):
                self.manifest = _Manifest(caps)

        self.o.plugin_registry.loaded_modules = {
            "dialog_memory_recall": _Mod(["memory", "dialogue"]),
            "chat-orchestrator": _Mod(["general"]),
        }
        selected = self.o._select_module("dialog_recall", {"dialog_memory_recall", "chat-orchestrator"})
        self.assertEqual(selected, "dialog_memory_recall")


if __name__ == "__main__":
    unittest.main()
