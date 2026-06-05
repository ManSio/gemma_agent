import unittest

from core.unified_planner import UnifiedPlanner


class TestUnifiedPlannerPassthrough(unittest.TestCase):
    def test_unknown_generate_module_routes_to_chat(self):
        p = UnifiedPlanner()
        allowed = {"chat_orchestrator", "math"}
        d = p.decide(
            text="/generate_module foo bar",
            allowed_modules=allowed,
            route_command=lambda raw, a: None,
            detect_intent=lambda t: "general",
            select_module=lambda intent, a: None,
        )
        self.assertFalse(d.fallback)
        self.assertEqual(d.module_name, "chat_orchestrator")
        self.assertIn("passthrough", d.reason)


if __name__ == "__main__":
    unittest.main()
