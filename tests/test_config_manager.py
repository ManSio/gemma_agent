import unittest

from core.config_manager import AppConfig


class ConfigManagerTests(unittest.TestCase):
    def test_new_engine_config_fields_validate(self):
        cfg = AppConfig()
        result = cfg.validate()
        self.assertIn("ok", result)
        self.assertTrue(hasattr(cfg, "predictive_behavior_enabled"))
        self.assertTrue(hasattr(cfg, "goal_engine_enabled"))
        self.assertTrue(hasattr(cfg, "self_maintenance_enabled"))
        self.assertTrue(hasattr(cfg, "self_improvement_advisor_enabled"))


if __name__ == "__main__":
    unittest.main()
