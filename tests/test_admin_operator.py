import unittest

from core.admin_module import AdminModule


class _FakeOrchestrator:
    anti_flood_enabled = True
    max_msg_per_10s = 7
    max_same_text = 3
    max_cmd_per_10s = 4
    group_cooldown_sec = 2.0

    def get_system_info(self):
        return {
            "overall_status": "healthy",
            "mode": "runtime",
            "planner": {"engine": "unified_planner_v1"},
        }


class AdminOperatorTests(unittest.TestCase):
    def test_operator_snapshot_shape(self):
        adm = AdminModule(orchestrator=_FakeOrchestrator(), behavior_store=None)
        snap = adm.operator_console_snapshot()
        self.assertIn("health", snap)
        self.assertIn("voice_stt", snap)
        self.assertIn("config_validation", snap)
        self.assertIn("operator_notes", snap)
        self.assertIn("openai_key_configured", snap["voice_stt"])
        self.assertIn("openrouter_key_configured", snap["voice_stt"])
        self.assertIn("stt_fallback_auto_openrouter", snap["voice_stt"])
        self.assertIn("mem0", snap)
        self.assertIn("telegram_runtime_settings", snap)
        self.assertIsInstance(snap["telegram_runtime_settings"], dict)


if __name__ == "__main__":
    unittest.main()
