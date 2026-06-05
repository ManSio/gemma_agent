"""OpenRouter SSE reasoning chunks + admin stream TG."""

import json
import os
import unittest
from unittest.mock import patch

from core.openrouter_reasoning import build_reasoning_map
from core.openrouter_stream import parse_openrouter_sse_chunk
from core.telegram_stream_reasoning import (
    admin_stream_reasoning_effective,
    arm_admin_stream_reasoning,
    compose_stream_display,
    disarm_admin_stream_reasoning,
    stream_reasoning_armed,
)
from core.telegram_stream_reply import telegram_stream_should_bind


class OpenRouterStreamReasoningParseTests(unittest.TestCase):
    def test_reasoning_details_delta(self):
        payload = {
            "choices": [
                {
                    "delta": {
                        "reasoning_details": [
                            {"type": "reasoning.text", "text": "Step one. "},
                            {"type": "reasoning.text", "text": "Step two."},
                        ]
                    }
                }
            ]
        }
        line = "data: " + json.dumps(payload)
        chunk = parse_openrouter_sse_chunk(line)
        self.assertEqual(chunk.reasoning, "Step one.Step two.")
        self.assertEqual(chunk.content, "")

    def test_reasoning_string_delta(self):
        payload = {"choices": [{"delta": {"reasoning": "think", "content": "Hi"}}]}
        line = "data: " + json.dumps(payload)
        chunk = parse_openrouter_sse_chunk(line)
        self.assertEqual(chunk.reasoning, "think")
        self.assertEqual(chunk.content, "Hi")


class AdminStreamReasoningTests(unittest.TestCase):
    def tearDown(self):
        disarm_admin_stream_reasoning()

    def test_compose_display(self):
        out = compose_stream_display(reasoning="думаю", content="ответ")
        self.assertIn("🧠", out)
        self.assertIn("думаю", out)
        self.assertIn("ответ", out)
        self.assertIn("—", out)

    def test_armed_changes_exclude(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_REASONING_ENABLED": "true",
                "OPENROUTER_REASONING_EXCLUDE": "true",
            },
            clear=False,
        ):
            disarm_admin_stream_reasoning()
            off = build_reasoning_map(tag="brain_first", model="deepseek/deepseek-v4-flash")
            self.assertTrue(off and off.get("exclude") is True)
            arm_admin_stream_reasoning(True)
            self.assertTrue(stream_reasoning_armed())
            on = build_reasoning_map(tag="brain_first", model="deepseek/deepseek-v4-flash")
            self.assertTrue(on and on.get("exclude") is False)

    def test_direct_dialog_reasoning_when_armed(self):
        with patch.dict(os.environ, {"OPENROUTER_REASONING_ENABLED": "true"}, clear=False):
            disarm_admin_stream_reasoning()
            self.assertIsNone(
                build_reasoning_map(tag="brain_direct_dialog", model="deepseek/deepseek-v4-flash")
            )
            arm_admin_stream_reasoning(True)
            block = build_reasoning_map(
                tag="brain_direct_dialog",
                model="deepseek/deepseek-v4-flash",
            )
            self.assertIsNotNone(block)
            self.assertFalse(block.get("exclude"))

    def test_should_bind_admin_reasoning_private(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_STREAM_REPLY_ENABLED": "true",
                "TELEGRAM_ADMIN_STREAM_REASONING": "true",
                "TELEGRAM_STREAM_DIRECT_ONLY": "true",
                "BRAIN_DIRECT_DIALOG_ENABLED": "true",
                "BRAIN_CHAT_AGENT_MODE": "false",
            },
            clear=False,
        ):
            self.assertTrue(
                telegram_stream_should_bind(
                    user_text="x" * 900,
                    is_group=False,
                    user_id="1",
                    is_admin=True,
                )
            )
            self.assertFalse(
                telegram_stream_should_bind(
                    user_text="x" * 900,
                    is_group=False,
                    user_id="1",
                    is_admin=False,
                )
            )

    def test_non_admin_flag_off(self):
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_STREAM_REASONING": "false"}, clear=False):
            self.assertFalse(admin_stream_reasoning_effective(is_admin=True))


if __name__ == "__main__":
    unittest.main()
