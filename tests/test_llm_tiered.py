import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm_tiered import llm_generate_tiered, _adaptive_timeout_sec, _result_usable


class TestLlmTiered(unittest.TestCase):
    def test_result_usable(self):
        self.assertFalse(_result_usable(None))
        self.assertFalse(_result_usable({"error": "x"}))
        self.assertFalse(_result_usable({"content": "  "}))
        self.assertTrue(_result_usable({"content": "ok"}))
        self.assertFalse(
            _result_usable({"content": "Извините, но я не могу помочь с этим запросом."})
        )

    @patch.dict(
        os.environ,
        {
            "BRAIN_LLM_COMPLEX_TIMEOUT_ENABLED": "true",
            "BRAIN_LLM_TIMEOUT_FACTOR_DEEP": "1.6",
            "BRAIN_LLM_TIMEOUT_PROMPT_CHARS_THRESHOLD": "10",
            "BRAIN_LLM_TIMEOUT_FACTOR_LONG_PROMPT": "1.4",
            "BRAIN_LLM_FREE_TIMEOUT_MAX_SEC": "360",
        },
        clear=False,
    )
    def test_adaptive_timeout_increases_for_complex_prompt(self):
        out = _adaptive_timeout_sec(
            30.0,
            task_tier="deep",
            prompt="x" * 20,
            max_tokens=512,
            tag="llm_first_stage",
            premium=False,
        )
        self.assertGreaterEqual(out, 42.0)

    @patch.dict(
        os.environ,
        {
            "BRAIN_LLM_TIERED_RETRY": "true",
            "BRAIN_LLM_FREE_ATTEMPTS": "2",
            "BRAIN_LLM_FREE_TIMEOUT_SEC": "30",
            "BRAIN_LLM_WAIT_BEFORE_PREMIUM_SEC": "0",
            "BRAIN_LLM_PREMIUM_TIMEOUT_SEC": "30",
            "BRAIN_LLM_FREE_RETRY_GAP_SEC": "0",
            "BRAIN_LLM_FREE_MODEL": "free/test",
            "BRAIN_LLM_PREMIUM_MODEL": "paid/test",
            "BRAIN_LLM_USE_DEV_KEY_FOR_PREMIUM": "true",
        },
        clear=False,
    )
    def test_escalates_to_premium_after_empty_free(self):
        llm = MagicMock()
        llm.free_model = "openrouter/free"
        llm.qwen_model = "qwen/x"
        llm.dev_model = "dev/x"
        llm.api_key = "main_k"
        llm.api_key_dev = "dev_k"

        seq = [
            {"success": True, "content": ""},
            {"success": True, "content": ""},
            {"success": True, "content": "final"},
        ]
        llm.generate = AsyncMock(side_effect=seq)

        import asyncio

        async def _run():
            return await llm_generate_tiered(
                llm,
                tag="t",
                prompt="p",
                system_prompt="s",
                max_tokens=10,
                temperature=0.5,
            )

        out = asyncio.run(_run())
        self.assertTrue(_result_usable(out))
        self.assertEqual(out.get("content"), "final")
        self.assertEqual(llm.generate.call_count, 3)
        # premium вызов с dev-ключом
        last_kw = llm.generate.call_args_list[-1].kwargs
        self.assertEqual(last_kw.get("model"), "paid/test")
        self.assertEqual(last_kw.get("api_key_override"), "dev_k")

    @patch.dict(
        os.environ,
        {
            "BRAIN_LLM_TIERED_RETRY": "true",
            "BRAIN_LLM_FREE_ATTEMPTS": "1",
            "BRAIN_LLM_FREE_TIMEOUT_SEC": "30",
            "BRAIN_LLM_WAIT_BEFORE_PREMIUM_SEC": "0",
            "BRAIN_LLM_PREMIUM_TIMEOUT_SEC": "30",
            "BRAIN_LLM_FREE_MODEL": "free/test",
            "BRAIN_LLM_PREMIUM_MODEL": "paid/test",
            "BRAIN_LLM_USE_DEV_KEY_FOR_PREMIUM": "true",
        },
        clear=False,
    )
    def test_escalates_to_premium_after_refusal_free(self):
        llm = MagicMock()
        llm.free_model = "openrouter/free"
        llm.qwen_model = "qwen/x"
        llm.dev_model = "dev/x"
        llm.api_key = "main_k"
        llm.api_key_dev = "dev_k"

        seq = [
            {"success": True, "content": "Извините, но я не могу помочь с этим запросом."},
            {"success": True, "content": "После родов близость лучше начинать с нежности."},
        ]
        llm.generate = AsyncMock(side_effect=seq)

        import asyncio

        async def _run():
            return await llm_generate_tiered(
                llm,
                tag="t_refusal",
                prompt="unique_refusal_prompt",
                system_prompt="s_refusal",
                max_tokens=10,
                temperature=0.5,
            )

        out = asyncio.run(_run())
        self.assertEqual(out.get("content"), "После родов близость лучше начинать с нежности.")
        self.assertEqual(llm.generate.call_count, 2)
        self.assertEqual(llm.generate.call_args_list[-1].kwargs.get("model"), "paid/test")


if __name__ == "__main__":
    unittest.main()
