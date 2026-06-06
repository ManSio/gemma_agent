import unittest

from core.lookahead_planner import build_lookahead_plan


class LookaheadPlannerModesTests(unittest.TestCase):
    def _base(self, **kwargs):
        return build_lookahead_plan(
            user_text=kwargs.get("user_text", ""),
            intent=kwargs.get("intent", "general"),
            module=kwargs.get("module", "chat-orchestrator"),
            planner_reason=kwargs.get("planner_reason", "intent_module_match"),
            fallback=kwargs.get("fallback", False),
            goal_hints=kwargs.get("goal_hints", {}),
            predictive_hint=kwargs.get("predictive_hint", {}),
            knowledge_hint=kwargs.get("knowledge_hint", {}),
            skill_name=kwargs.get("skill_name", ""),
        )

    def test_test_mode(self):
        p = self._base(user_text="Запусти F-series test", intent="test")
        self.assertEqual(p.get("planner_mode"), "TEST_MODE")
        self.assertTrue(any("test-report" in str(s.get("do") or "").lower() for s in p.get("steps") or []))

    def test_reasoning_mode(self):
        p = self._base(user_text="Разбери δ-игру", intent="reasoning")
        self.assertEqual(p.get("planner_mode"), "REASONING_MODE")
        self.assertTrue(any("цепочк" in str(s.get("do") or "").lower() for s in p.get("steps") or []))

    def test_explain_mode(self):
        p = self._base(user_text="Объясни это просто", intent="explain")
        self.assertEqual(p.get("planner_mode"), "EXPLAIN_MODE")
        self.assertTrue(any("объяснен" in str(s.get("why") or "").lower() for s in p.get("steps") or []))

    def test_noise_mode(self):
        p = self._base(user_text="ок", intent="general")
        self.assertEqual(p.get("planner_mode"), "NOISE_MODE")
        self.assertTrue(any("контекст" in str(s.get("do") or "").lower() for s in p.get("steps") or []))


if __name__ == "__main__":
    unittest.main()
