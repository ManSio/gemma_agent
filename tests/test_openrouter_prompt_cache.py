import os
import unittest
from unittest.mock import patch

from core.openrouter_prompt_cache import extra_completion_body_fields


class OpenRouterPromptCacheTests(unittest.TestCase):
    def test_off_by_default(self):
        with patch.dict(os.environ, {"OPENROUTER_PROMPT_CACHE_MODE": "off"}, clear=False):
            self.assertEqual(extra_completion_body_fields("anthropic/claude-sonnet-4"), {})

    def test_anthropic_auto_injects_cache_control(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_PROMPT_CACHE_MODE": "anthropic_auto", "OPENROUTER_ANTHROPIC_CACHE_TTL": ""},
            clear=False,
        ):
            d = extra_completion_body_fields("anthropic/claude-sonnet-4.6")
            self.assertEqual(d.get("cache_control"), {"type": "ephemeral"})

    def test_anthropic_ttl_1h(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_PROMPT_CACHE_MODE": "anthropic_auto", "OPENROUTER_ANTHROPIC_CACHE_TTL": "1h"},
            clear=False,
        ):
            d = extra_completion_body_fields("anthropic/claude-sonnet-4.6")
            self.assertEqual(d.get("cache_control"), {"type": "ephemeral", "ttl": "1h"})

    def test_deepseek_default_allow_fallbacks(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_PROMPT_CACHE_MODE": "off",
                "OPENROUTER_CACHE_FIRST_PROVIDERS": "false",
            },
            clear=False,
        ):
            self.assertEqual(
                extra_completion_body_fields("deepseek/deepseek-v4-flash"),
                {"provider": {"allow_fallbacks": True}},
            )

    def test_cache_first_auto_mode_deepseek(self):
        env = {
            "OPENROUTER_PROMPT_CACHE_MODE": "auto",
            "OPENROUTER_PROVIDER_ORDER": "",
            "OPENROUTER_PROVIDER_IGNORE": "",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("OPENROUTER_CACHE_FIRST_PROVIDERS", None)
            d = extra_completion_body_fields("deepseek/deepseek-v4-flash")
            self.assertEqual(
                d.get("provider"),
                {
                    "order": ["DeepSeek", "baidu"],
                    "ignore": ["deepinfra"],
                    "allow_fallbacks": True,
                },
            )

    def test_fp8_skipped_for_free_router_model(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_PROVIDER_ORDER": "baidu,DeepSeek",
                "OPENROUTER_PROVIDER_QUANTIZATIONS": "fp8",
                "OPENROUTER_PROVIDER_IGNORE": "deepinfra",
                "OPENROUTER_PROVIDER_ALLOW_FALLBACKS": "true",
                "OPENROUTER_CACHE_FIRST_PROVIDERS": "false",
            },
            clear=False,
        ):
            d = extra_completion_body_fields("liquid/lfm-2.5-1.2b-instruct:free")
            prov = d.get("provider") or {}
            self.assertNotIn("quantizations", prov)
            self.assertEqual(prov.get("order"), ["baidu", "DeepSeek"])

    def test_provider_order_from_env(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_PROVIDER_ORDER": "baidu,DeepSeek",
                "OPENROUTER_PROVIDER_QUANTIZATIONS": "fp8",
                "OPENROUTER_PROVIDER_IGNORE": "deepinfra",
                "OPENROUTER_PROVIDER_ALLOW_FALLBACKS": "true",
            },
            clear=False,
        ):
            d = extra_completion_body_fields("deepseek/deepseek-v4-flash")
            self.assertEqual(
                d.get("provider"),
                {
                    "order": ["baidu", "DeepSeek"],
                    "quantizations": ["fp8"],
                    "ignore": ["deepinfra"],
                    "allow_fallbacks": True,
                },
            )


if __name__ == "__main__":
    unittest.main()
