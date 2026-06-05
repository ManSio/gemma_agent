import os
import unittest
from unittest.mock import patch

from core.openrouter_completion_text import text_from_completion_choice, user_facing_completion_text


class OpenRouterCompletionTextTests(unittest.TestCase):
    def test_string_content(self):
        self.assertEqual(
            text_from_completion_choice({"message": {"role": "assistant", "content": " hi "}}),
            "hi",
        )

    def test_multipart_text(self):
        ch = {
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": " world"},
                ],
            }
        }
        self.assertEqual(text_from_completion_choice(ch), "Hello world")

    def test_reasoning_not_exposed_by_default(self):
        ch = {"message": {"content": "", "reasoning": "internal only"}}
        self.assertEqual(text_from_completion_choice(ch), "")

    def test_reasoning_fallback_when_flag(self):
        ch = {"message": {"content": "", "reasoning": "OK"}}
        with patch.dict(os.environ, {"OPENROUTER_EXPOSE_REASONING": "true"}, clear=False):
            self.assertEqual(text_from_completion_choice(ch), "OK")

    def test_reasoning_when_include_reasoning_kwarg(self):
        ch = {"message": {"content": "", "reasoning": "OK"}}
        self.assertEqual(text_from_completion_choice(ch, include_reasoning=True), "OK")

    def test_non_chat_choice_text(self):
        self.assertEqual(text_from_completion_choice({"text": "legacy", "finish_reason": "stop"}), "legacy")

    def test_message_wins_over_choice_text(self):
        ch = {"message": {"content": "a"}, "text": "b"}
        self.assertEqual(text_from_completion_choice(ch), "a")

    def test_invalid_choice(self):
        self.assertEqual(text_from_completion_choice(None), "")
        self.assertEqual(text_from_completion_choice("x"), "")

    def test_user_facing_reasoning_fallback_deepseek(self):
        ch = {"message": {"content": "", "reasoning": "Краткий ответ пользователю."}}
        with patch.dict(os.environ, {"OPENROUTER_REASONING_FALLBACK_IF_EMPTY": "true"}, clear=False):
            out = user_facing_completion_text(ch, requested_model="deepseek/deepseek-v4-flash")
        self.assertIn("Краткий", out)

    def test_user_facing_skips_r1(self):
        ch = {"message": {"content": "", "reasoning": "long cot"}}
        with patch.dict(os.environ, {"OPENROUTER_REASONING_FALLBACK_IF_EMPTY": "true"}, clear=False):
            out = user_facing_completion_text(ch, requested_model="deepseek/deepseek-r1-0528")
        self.assertEqual(out, "")

    def test_user_facing_rejects_json_reasoning_leak(self):
        ch = {
            "message": {
                "content": "",
                "reasoning": '"..."}], ... (сокращённо до 12000 символов)',
            }
        }
        with patch.dict(os.environ, {"OPENROUTER_REASONING_FALLBACK_IF_EMPTY": "true"}, clear=False):
            out = user_facing_completion_text(ch, requested_model="deepseek/deepseek-v4-flash")
        self.assertEqual(out, "")

    def test_user_facing_strip_redacted_thinking(self):
        ch = {
            "message": {
                "content": "",
                "reasoning": "<think>x</think>Итог для пользователя.",
            }
        }
        with patch.dict(os.environ, {"OPENROUTER_REASONING_FALLBACK_IF_EMPTY": "true"}, clear=False):
            out = user_facing_completion_text(ch, requested_model="deepseek/deepseek-v4-flash")
        self.assertIn("Итог", out)
        self.assertNotIn("redacted", out.lower())

    def test_user_facing_strip_orphan_thinking_close_tag(self):
        ch = {
            "message": {
                "content": "</think>Давай. С чего начнём?",
                "reasoning": "",
            }
        }
        out = user_facing_completion_text(ch, requested_model="google/gemini-2.0-flash")
        self.assertIn("Давай", out)
        self.assertNotIn("redacted", out.lower())


if __name__ == "__main__":
    unittest.main()
