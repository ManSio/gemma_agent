"""Self-model limits, .env merge, prompt addon."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core import self_model as sm


class SelfModelLimitsTests(unittest.TestCase):
    def test_merge_defaults_true(self):
        with patch.dict(os.environ, {}, clear=False):
            env_pop = (
                "SELF_MODEL_NO_FORCE_EXTERNAL_STATE",
                "SELF_MODEL_CONTEXT_IS_PROBABILISTIC",
            )
            for k in env_pop:
                os.environ.pop(k, None)
        m = sm.merge_limits_effective({})
        self.assertTrue(m["limits"]["no_force_external_state"])
        self.assertTrue(m["limits"]["context_is_probabilistic"])

    def test_env_overrides(self):
        with patch.dict(
            os.environ,
            {
                "SELF_MODEL_NO_FORCE_EXTERNAL_STATE": "false",
                "SELF_MODEL_CONTEXT_IS_PROBABILISTIC": "0",
            },
            clear=False,
        ):
            m = sm.merge_limits_effective({"limits": {"no_force_external_state": True, "context_is_probabilistic": True}})
        self.assertFalse(m["limits"]["no_force_external_state"])
        self.assertFalse(m["limits"]["context_is_probabilistic"])

    def test_addon_reflects_flags(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ("SELF_MODEL_NO_FORCE_EXTERNAL_STATE", "SELF_MODEL_CONTEXT_IS_PROBABILISTIC"):
                os.environ.pop(k, None)
        with patch.dict(os.environ, {"SELF_MODEL_ENABLED": "true", "SELF_MODEL_PROMPT_ADDON_ENABLED": "true"}):
            cautious = sm.self_model_trust_addon_for_prompt({"limits": {"no_force_external_state": True, "context_is_probabilistic": True}})
            self.assertIn("не считай абсолютной", cautious.lower())
            confident = sm.self_model_trust_addon_for_prompt(
                {"limits": {"no_force_external_state": False, "context_is_probabilistic": False}}
            )
            self.assertIn("напрямую", confident.lower())


if __name__ == "__main__":
    unittest.main()
