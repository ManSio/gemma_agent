import os
import unittest
from unittest.mock import patch

from core.autopilot_mode import apply_autopilot_defaults, autopilot_enabled


class AutopilotModeTests(unittest.TestCase):
    def test_disabled_no_changes(self):
        with patch.dict(os.environ, {"GEMMA_AUTOPILOT_MODE": ""}, clear=False):
            out = apply_autopilot_defaults()
        self.assertEqual(out, {})

    def test_enabled_sets_missing(self):
        # Изолированное окружение: иначе наследуемый LOG_FORMAT из среды скрывает дефолт автопилота.
        with patch.dict(os.environ, {"GEMMA_AUTOPILOT_MODE": "on"}, clear=True):
            out = apply_autopilot_defaults()
            self.assertTrue(autopilot_enabled())
            self.assertNotIn("GEMMA_CORE_LOG_FULL", out)
            self.assertIsNone(os.getenv("GEMMA_CORE_LOG_FULL"))
            self.assertIn("LOG_FORMAT", out)
            self.assertEqual(os.getenv("LOG_FORMAT"), "plain")
            self.assertIn("LATENCY_TRACE_LOG", out)
            self.assertEqual(os.getenv("LATENCY_TRACE_LOG"), "slow")
            self.assertEqual(os.getenv("STRATEGY_LLM_OUTLINE_ENABLED"), "false")
            self.assertEqual(os.getenv("BRAIN_TOOL_CHAIN_MAX"), "0")
            self.assertIn("STRATEGY_LLM_OUTLINE_ENABLED", out)
            self.assertIn("BRAIN_TOOL_CHAIN_MAX", out)

    def test_does_not_override_explicit(self):
        with patch.dict(
            os.environ,
            {"GEMMA_AUTOPILOT_MODE": "on", "LOG_FORMAT": "console"},
            clear=False,
        ):
            out = apply_autopilot_defaults()
            self.assertEqual(os.getenv("LOG_FORMAT"), "console")
            self.assertNotIn("LOG_FORMAT", out)


if __name__ == "__main__":
    unittest.main()

