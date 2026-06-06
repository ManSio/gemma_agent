"""Регрессия: self_verify_pass (вынесено из pipeline)."""
from __future__ import annotations

import os
import unittest

from core.brain.self_verify_pass import (
    looks_like_garbage_json,
    self_verify_fix_quality,
    should_self_verify,
)


class BrainSelfVerifyPassTests(unittest.TestCase):
    def test_garbage_json_detects_object(self) -> None:
        self.assertTrue(looks_like_garbage_json('{"foo": 1, "bar": "x"}'))
        self.assertFalse(looks_like_garbage_json("TOOL_CALL: Foo.bar"))
        self.assertFalse(looks_like_garbage_json("Короткий ответ по-русски."))

    def test_fix_quality_min_length(self) -> None:
        self.assertFalse(self_verify_fix_quality("коротко"))
        long_fix = "Исправление: пользователь спрашивал про время. Сейчас 12:00 UTC."
        self.assertTrue(self_verify_fix_quality(long_fix))

    def test_should_self_verify_off_by_default(self) -> None:
        os.environ.pop("SELF_VERIFY_ACTIVE", None)
        self.assertFalse(should_self_verify("standard"))


if __name__ == "__main__":
    unittest.main()
