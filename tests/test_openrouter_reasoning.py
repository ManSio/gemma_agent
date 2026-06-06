import os
import unittest
from unittest.mock import patch

from core.openrouter_reasoning import apply_openrouter_reasoning, build_reasoning_map


class OpenRouterReasoningTests(unittest.TestCase):
    def _env(self, **kw):
        base = {
            "OPENROUTER_REASONING_ENABLED": "true",
            "OPENROUTER_REASONING_EXCLUDE": "true",
            "OPENROUTER_BRAIN_REASONING_EFFORT": "high",
            "OPENROUTER_GEN_REASONING_EFFORT": "none",
        }
        base.update(kw)
        return patch.dict(os.environ, base, clear=False)

    def test_brain_first_high_exclude(self):
        with self._env():
            block = build_reasoning_map(
                tag="brain_first",
                model="deepseek/deepseek-v4-flash",
                max_tokens=1536,
            )
        self.assertEqual(block, {"effort": "high", "exclude": True})

    def test_brain_direct_dialog_skipped(self):
        with self._env():
            block = build_reasoning_map(
                tag="brain_direct_dialog",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertIsNone(block)

    def test_non_brain_no_global_effort(self):
        with self._env(OPENROUTER_GEN_REASONING_EFFORT="none"):
            block = build_reasoning_map(
                tag="router_classifier",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertIsNone(block)

    def test_global_medium_for_non_brain_when_set(self):
        with self._env(OPENROUTER_GEN_REASONING_EFFORT="medium"):
            block = build_reasoning_map(
                tag="news_digest_llm_narrative",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertEqual(block, {"effort": "medium", "exclude": True})

    def test_non_deepseek_model_skipped(self):
        with self._env():
            block = build_reasoning_map(
                tag="brain_first",
                model="google/gemma-3-12b-it",
            )
        self.assertIsNone(block)

    def test_apply_strips_legacy_reasoning_effort(self):
        with self._env():
            payload = {
                "model": "deepseek/deepseek-v4-pro",
                "max_tokens": 1536,
                "reasoning_effort": "medium",
            }
            apply_openrouter_reasoning(payload, tag="brain_second")
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["reasoning"]["effort"], "high")
        self.assertTrue(payload["reasoning"]["exclude"])

    def test_high_effort_bumps_low_max_tokens(self):
        with self._env(OPENROUTER_REASONING_HIGH_MIN_MAX_TOKENS="2048"):
            payload = {
                "model": "deepseek/deepseek-v4-flash",
                "max_tokens": 1200,
            }
            apply_openrouter_reasoning(payload, tag="brain_first")
        self.assertGreaterEqual(payload["max_tokens"], 2048)

    def test_tag_override_xhigh(self):
        with self._env(OPENROUTER_GEN_BRAIN_FIRST_REASONING_EFFORT="xhigh"):
            block = build_reasoning_map(
                tag="brain_first",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertEqual(block["effort"], "xhigh")

    def test_reasoning_max_tokens_instead_of_effort(self):
        with self._env(OPENROUTER_GEN_BRAIN_FIRST_REASONING_MAX_TOKENS="900"):
            block = build_reasoning_map(
                tag="brain_first",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertEqual(block, {"max_tokens": 900, "exclude": True})

    def test_master_switch_off(self):
        with self._env(OPENROUTER_REASONING_ENABLED="false"):
            block = build_reasoning_map(
                tag="brain_first",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertIsNone(block)

    def test_tag_enabled_without_effort(self):
        with self._env(
            OPENROUTER_GEN_REASONING_EFFORT="none",
            OPENROUTER_BRAIN_REASONING_EFFORT="none",
            OPENROUTER_GEN_NEWS_DIGEST_LLM_NARRATIVE_REASONING_ENABLED="true",
        ):
            block = build_reasoning_map(
                tag="news_digest_llm_narrative",
                model="deepseek/deepseek-v4-flash",
            )
        self.assertEqual(block, {"enabled": True, "exclude": True})


if __name__ == "__main__":
    unittest.main()
