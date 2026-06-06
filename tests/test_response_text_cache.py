import os
import unittest
from unittest.mock import patch

from core.models import Output
from core.response_text_cache import (
    build_hit_keyboard,
    get_hit,
    maybe_store,
    lookup_record,
    reset_for_tests,
)


class ResponseTextCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_for_tests()

    def tearDown(self) -> None:
        reset_for_tests()

    def test_math_roundtrip_and_replay_lookup(self):
        with patch.dict(
            os.environ,
            {"BRAIN_RESPONSE_CACHE_ENABLED": "true", "BRAIN_RESPONSE_CACHE_TTL_SEC": "600"},
            clear=False,
        ):
            reset_for_tests()
            maybe_store(
                user_id="1",
                chat_id="99",
                replay_payload="/calc 2+2",
                input_meta={},
                module_name="math",
                outputs=[
                    Output(
                        type="text",
                        payload="Результат: 4",
                        meta={"module": "math", "expression": "2+2"},
                    )
                ],
            )
            h = get_hit("1", "99", "/calc 2+2", {})
            self.assertIsNotNone(h)
            assert h is not None
            self.assertIn("4", h["text"])
            rid = h["record_id"]
            ent = lookup_record(rid)
            self.assertIsNotNone(ent)
            assert ent is not None
            self.assertEqual(ent["user_id"], "1")
            self.assertIn("2+2", ent["replay_payload"])

    def test_skip_reply_context(self):
        with patch.dict(os.environ, {"BRAIN_RESPONSE_CACHE_ENABLED": "true"}, clear=False):
            reset_for_tests()
            maybe_store(
                user_id="1",
                chat_id="99",
                replay_payload="/calc 1+1",
                input_meta={},
                module_name="math",
                outputs=[Output(type="text", payload="Результат: 2", meta={"module": "math"})],
            )
            h = get_hit("1", "99", "/calc 1+1", {"telegram_reply_context": "x"})
            self.assertIsNone(h)

    def test_skip_error_math(self):
        with patch.dict(os.environ, {"BRAIN_RESPONSE_CACHE_ENABLED": "true"}, clear=False):
            reset_for_tests()
            maybe_store(
                user_id="1",
                chat_id="99",
                replay_payload="/calc oops",
                input_meta={},
                module_name="math",
                outputs=[
                    Output(
                        type="text",
                        payload="Ошибка",
                        meta={"module": "math", "error": "x"},
                    )
                ],
            )
            h = get_hit("1", "99", "/calc oops", {})
            self.assertIsNone(h)

    def test_keyboard_math_label(self):
        kb = build_hit_keyboard("math", "abcdabcdabcdabcd")
        self.assertTrue(kb.inline_keyboard)
        self.assertIn("Пересчитать", kb.inline_keyboard[0][0].text)


if __name__ == "__main__":
    unittest.main()
