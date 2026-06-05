import unittest

from core.unified_planner import UnifiedPlanner
from modules.skills.router import skill_context_pack


class UnifiedPlannerTests(unittest.TestCase):
    def setUp(self):
        self.p = UnifiedPlanner()
        self.allowed = {"chat_orchestrator", "math"}

    def test_command_route(self):
        d = self.p.decide(
            text="/calc 2+2",
            allowed_modules=self.allowed,
            route_command=lambda t, a: "math",
            detect_intent=lambda t: "general",
            select_module=lambda i, a: None,
            input_meta={},
            knowledge_hint={},
        )
        self.assertEqual(d.module_name, "math")
        self.assertEqual(d.intent, "command")

    def test_rich_context_prefers_chat_orchestrator(self):
        d = self.p.decide(
            text="проанализируй документ",
            allowed_modules=self.allowed,
            route_command=lambda t, a: None,
            detect_intent=lambda t: "general",
            select_module=lambda i, a: None,
            input_meta={"document_intake": {"ok": True}},
            knowledge_hint={"policy": "fresh_trusted_tagged", "confidence": 0.8},
        )
        self.assertEqual(d.module_name, "chat_orchestrator")
        self.assertIn("rich_context", d.reason)
        self.assertIn("kh:fresh_trusted_tagged", d.reason)

    def test_knowledge_hint_can_set_skill(self):
        d = self.p.decide(
            text="weather in my city",
            allowed_modules=self.allowed,
            route_command=lambda t, a: None,
            detect_intent=lambda t: "weather",
            select_module=lambda i, a: "chat_orchestrator",
            input_meta={},
            knowledge_hint={"policy": "fresh_trusted_tagged", "confidence": 0.9},
        )
        self.assertEqual(d.module_name, "chat_orchestrator")
        self.assertEqual(d.skill_name, "weather")


class SkillContextPackTests(unittest.TestCase):
    def test_passes_situation_dict(self):
        ctx = {"dialogue_state": {}, "situation": {"schema": "situation_v1", "plan_mode": "full"}}
        pack = skill_context_pack(ctx)
        self.assertEqual(pack.get("situation", {}).get("schema"), "situation_v1")

    def test_non_dict_situation_becomes_empty(self):
        pack = skill_context_pack({"situation": "bad"})
        self.assertEqual(pack.get("situation"), {})


if __name__ == "__main__":
    unittest.main()
