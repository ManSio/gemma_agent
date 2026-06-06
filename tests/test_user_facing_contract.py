"""Контракт доставки ответа пользователю (не словари триггеров)."""

import unittest

from core.brain.user_facing_contract import (
    assistant_invites_continuation,
    classify_short_user_turn,
    is_short_turn_continuing_dialogue,
    normalize_user_facing_text,
    short_reply_acceptable_for_turn,
)


class UserFacingContractTests(unittest.TestCase):
    def test_local_models_hint_when_hf_thread(self):
        from core.brain.user_facing_contract import build_local_models_scope_hint

        rd = [
            {"role": "user", "text": "https://huggingface.co/foo/bar"},
            {"role": "assistant", "text": "краткий конспект модели"},
        ]
        hint = build_local_models_scope_hint("lm studio для картинок", rd)
        self.assertIn("DOMAIN_LOCAL_MODELS", hint)
        self.assertIn("LM Studio", hint)

    def test_recover_fallback_continuation(self):
        from core.brain.user_facing_contract import recover_delivery_fallback

        last = "могу подсказать, с чего начать."
        msg = recover_delivery_fallback("давай", [], last_assistant=last)
        self.assertNotIn("Не удалось сформировать нормальный", msg)
        self.assertGreater(len(msg), 20)

    def test_strip_orphan_thinking_tag(self):
        raw = "</think>Давай. С чего начнём?"
        res = normalize_user_facing_text(raw, user_text="давай")
        self.assertEqual(res.status, "ok")
        self.assertIn("Давай", res.text)
        self.assertNotIn("redacted", res.text.lower())

    def test_agreement_after_bot_offer(self):
        last = (
            "Если тебе нужна готовая сборка для генерации изображений на ПК, "
            "могу подсказать, с чего начать."
        )
        self.assertTrue(assistant_invites_continuation(last))
        kind = classify_short_user_turn("давац", [], last_assistant=last)
        self.assertEqual(kind, "agreement")
        self.assertTrue(is_short_turn_continuing_dialogue(kind))

    def test_not_continuation_on_negation(self):
        kind = classify_short_user_turn(
            "не давай мне советов",
            [],
            last_assistant="могу подсказать, с чего начать",
        )
        self.assertEqual(kind, "normal")

    def test_explicit_continue(self):
        kind = classify_short_user_turn("Продолжи", [])
        self.assertEqual(kind, "continuation")

    def test_short_reply_acceptable(self):
        last = "могу подсказать, с чего начать."
        self.assertTrue(
            short_reply_acceptable_for_turn(
                "Давай. С чего начнём?",
                "давай",
                [],
                last_assistant=last,
            )
        )


if __name__ == "__main__":
    unittest.main()
