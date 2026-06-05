"""Рефлексия только на тяжёлых ходах — эвристика без LLM."""
from __future__ import annotations

import unittest

from core.heavy_response_reflection import (
    looks_like_reflection_meta_leak,
    should_reflect_heavy_turn,
    should_skip_heavy_reflection_for_meta,
)
from tests.test_math_investment import TASK as INVESTMENT_TASK


class TestHeavyResponseReflection(unittest.TestCase):
    def test_short_chitchat_no_reflect(self):
        self.assertFalse(
            should_reflect_heavy_turn(
                user_text="привет",
                reply="Привет! Чем помочь?",
                profile="standard",
            )
        )

    def test_long_user_reflect(self):
        self.assertTrue(
            should_reflect_heavy_turn(
                user_text="x" * 500,
                reply="короткий ответ",
                profile="standard",
            )
        )

    def test_spatial_design_skips_heavy_reflect(self):
        meta = {"module": "spatial_design", "phase": "awaiting_feedback"}
        self.assertTrue(should_skip_heavy_reflection_for_meta(meta))
        self.assertFalse(
            should_reflect_heavy_turn(
                user_text="x" * 500,
                reply="📐 План помещения — сверка перед визуализацией",
                profile="standard",
                output_meta=meta,
            )
        )

    def test_summarization_profile_reflect(self):
        self.assertTrue(
            should_reflect_heavy_turn(
                user_text="https://habr.com/ru/articles/1/",
                reply="Пересказ статьи " * 20,
                profile="summarization",
            )
        )

    def test_investment_task_no_heavy_reflect(self):
        self.assertFalse(
            should_reflect_heavy_turn(
                user_text=INVESTMENT_TASK,
                reply="Уравнение: 12=1. Коэффициент при x равен 0",
                profile="standard",
            )
        )

    def test_reflection_meta_leak_detected(self):
        bad = (
            "Мы получили запрос: Профиль: standard. Черновик ответа: Уравнение 12=1. "
            "Нужно улучшить черновик и вернуть ответ пользователю."
        )
        self.assertTrue(looks_like_reflection_meta_leak(bad))

    def test_pre_send_critical_reflect(self):
        self.assertTrue(
            should_reflect_heavy_turn(
                user_text="объясни",
                reply="ок",
                profile="standard",
                scenario_pre_hits=[{"action": "replace_fallback", "severity": "critical"}],
            )
        )


if __name__ == "__main__":
    unittest.main()
