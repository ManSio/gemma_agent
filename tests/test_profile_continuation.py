import unittest

from core.brain.cot_strip import _paragraph_is_format_leak
from core.brain.profile_registry import (
    is_continuation_turn,
    profile_from_text_heuristics,
    resolve_continuation_profile,
    resolve_tool_prefixes,
)


class ProfileContinuationTests(unittest.TestCase):
    def test_pythagoras_heuristic(self):
        p = profile_from_text_heuristics("расскажи про теорему Пифагора простыми словами")
        self.assertEqual(p, "quick_explain")

    def test_continuation_detect(self):
        self.assertTrue(is_continuation_turn("Продолжи"))
        offer = {
            "recent_dialogue": [
                {
                    "role": "assistant",
                    "text": "могу подсказать, с чего начать настройку LM Studio.",
                }
            ]
        }
        self.assertTrue(is_continuation_turn("давай", offer))
        self.assertTrue(is_continuation_turn("давац", offer))
        self.assertFalse(is_continuation_turn("расскажи про теорему Пифагора"))
        self.assertFalse(
            is_continuation_turn(
                "не давай мне советов",
                offer,
            )
        )

    def test_continuation_inherits_profile(self):
        ctx = {"dialogue_state": {"last_brain_profile": "quick_explain"}}
        self.assertEqual(resolve_continuation_profile("Продолжи", ctx), "quick_explain")

    def test_standard_tool_count_slim(self):
        prefixes = resolve_tool_prefixes("standard")
        self.assertIsNotNone(prefixes)
        self.assertLess(len(prefixes or []), 10)

    def test_persona_style_leak_filtered(self):
        leak = "Style:\n- blended_style_stable: {'name': 'инженерный', 'style': 'direct'}"
        self.assertTrue(_paragraph_is_format_leak(leak))

    def test_user_message_leak_filtered(self):
        leak = "User message:\nпомнишь, я тебе про сбои писал сегодня?\nJSON:"
        self.assertTrue(_paragraph_is_format_leak(leak))

    def test_short_followup_continuation(self):
        self.assertTrue(is_continuation_turn("Ну и что? где"))
        ctx = {"dialogue_state": {"last_brain_profile": "quick_explain"}}
        self.assertEqual(resolve_continuation_profile("В сельсовете?", ctx), "quick_explain")

    def test_reference_paste_not_batch(self):
        from core.brain.router_classifier import _detect_batch

        paste = (
            "С 1 января 2025 года в Беларуси действует новый порядок.\n" * 25
            + "Надеюсь, эта информация была полезной."
        )
        self.assertFalse(_detect_batch(paste))
