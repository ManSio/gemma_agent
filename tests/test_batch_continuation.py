"""Тесты разбора batch и единой задачи с подпунктами."""

from __future__ import annotations

from core.batch_continuation import (
    extract_items,
    is_unified_problem,
    looks_like_unified_math_problem,
    resolve_unified_problem_profile,
)
from core.brain.router_classifier import _detect_batch


_TESSERACT = """Ты находишься внутри четырёхмерного куба (тессеракта). Все его трёхмерные ячейки окрашены в разные цвета. Ты можешь перемещаться только через грани между соседними ячейками.

Вопросы:
1. Сколько всего трёхмерных ячеек у тессеракта?
2. Сколько граней нужно пересечь, чтобы выйти из куба наружу, если ты в центре?
3. Какой минимальный путь до выхода?

Ответь без рассуждений, только факты."""

_PENTERACT = """Ты находишься внутри пятимерного куба (пентеракта). Все его четырёхмерные ячейки окрашены в разные цвета. Ты можешь перемещаться только через трёхмерные грани между соседними четырёхмерными ячейками.

Вопросы:

1. Сколько всего четырёхмерных ячеек у пентеракта?
2. Сколько трёхмерных граней нужно пересечь, чтобы выйти из куба наружу, если ты находишься в центре?
3. Каков минимальный путь до выхода (количество шагов)?"""


class TestIsUnifiedProblem:
    def test_tesseract_is_unified(self):
        assert is_unified_problem(_TESSERACT) is True

    def test_multi_question_list_is_not_unified(self):
        lines = "\n".join(
            f"почему небо голубое {i}" for i in range(6)
        )
        assert is_unified_problem(lines) is False


class TestExtractItems:
    def test_tesseract_extracts_three_numbered_with_preamble(self):
        items = extract_items(_TESSERACT)
        assert len(items) == 3
        assert "тессеракт" in items[0].lower()
        assert "трёхмерных ячеек" in items[0].lower()
        assert all("тессеракт" in it.lower() for it in items)

    def test_multiline_independent_questions(self):
        text = "привет\nпочему небо голубое\nкак работает GPS\nзачем нужен сон"
        items = extract_items(text)
        assert len(items) >= 3
        assert "привет" in items[0]


class TestUnifiedMathProfile:
    def test_penteract_is_unified_math(self):
        assert is_unified_problem(_PENTERACT) is True
        assert looks_like_unified_math_problem(_PENTERACT) is True
        assert resolve_unified_problem_profile(_PENTERACT) == "math_solve"

    def test_tesseract_resolves_math_solve(self):
        assert resolve_unified_problem_profile(_TESSERACT) == "math_solve"


class TestDetectBatch:
    def test_tesseract_not_batch(self):
        assert _detect_batch(_TESSERACT) is False

    def test_penteract_not_batch(self):
        assert _detect_batch(_PENTERACT) is False

    def test_many_lines_still_batch(self):
        text = "\n".join(f"вопрос {i}?" for i in range(8))
        assert _detect_batch(text) is True
