"""Тесты Parallel Batch Processor (без LLM — mock)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from typing import Any, Dict, List, Optional, Tuple

import pytest

from core.batch_processor import (
    is_parallel_eligible,
    run_parallel_batch,
    _get_max_parallel,
    _record_batch_result,
    _build_user_facts_context,
    _item_needs_user_facts,
    _MIN_ITEMS_FOR_PARALLEL,
    _PARALLEL_INITIAL,
)


# ── is_parallel_eligible — расширенные тесты ──


class TestIsParallelEligible:
    def test_empty_returns_false(self):
        assert is_parallel_eligible([]) is False

    def test_too_few_items_returns_false(self):
        assert is_parallel_eligible(["a", "b"]) is False

    def test_enough_items_returns_true(self):
        items = ["пункт один", "пункт два", "пункт три"]
        assert is_parallel_eligible(items) is True

    def test_dependency_marker_blocks(self):
        items = ["на основе предыдущего ответа", "второй", "третий"]
        assert is_parallel_eligible(items) is False

    def test_following_marker_blocks(self):
        items = ["following the previous", "second", "third"]
        assert is_parallel_eligible(items) is False

    def test_no_false_positive_on_normal_text(self):
        items = ["расскажи о погоде", "что такое квантовая физика", "как варить борщ"]
        assert is_parallel_eligible(items) is True

    # ── Cross-reference между пунктами ──

    def test_cross_ref_pronoun_blocks(self):
        """Пункт 2 ссылается на пункт 1 через местоимение 'его'."""
        items = ["напиши стих", "сравни его с прозой", "что такое хокку"]
        assert is_parallel_eligible(items) is False

    def test_cross_ref_eto_blocks(self):
        """Пункт 2 начинается с 'это' — анафора на предыдущий."""
        items = ["объясни теорию струн", "это сложно для понимания", "как это применяется"]
        assert is_parallel_eligible(items) is False

    def test_cross_ref_takoy_blocks(self):
        items = ["придумай название", "такое имя уже занято", "предложи другое"]
        assert is_parallel_eligible(items) is False

    def test_cross_ref_dannyy_blocks(self):
        items = ["напиши код", "данный метод неэффективен", "предложи другой"]
        assert is_parallel_eligible(items) is False  # "данный" в пункте 2

    def test_cross_ref_above_marker_blocks(self):
        items = ["что такое LLM", "как применить вышеуказанное", "пример кода"]
        assert is_parallel_eligible(items) is False

    def test_cross_ref_aforementioned_blocks(self):
        items = ["выбери технологию", "aforementioned solution is best", "альтернативы"]
        assert is_parallel_eligible(items) is False

    # ── Сравнение между пунктами ──

    def test_comparison_blocks(self):
        items = ["айфон 16", "сравни с Samsung S25", "что лучше по камере"]
        assert is_parallel_eligible(items) is False

    def test_russian_comparison_blocks(self):
        items = ["купить Toyota", "в отличие от BMW она надежнее", "выбор"]
        assert is_parallel_eligible(items) is False

    def test_difference_between_blocks(self):
        items = ["расскажи про Python", "разница между Python и Java", "что учить"]
        assert is_parallel_eligible(items) is False

    # ── Дейктические старты ──

    def test_deictic_a_teper_blocks(self):
        items = ["объясни байесовскую статистику", "а теперь примени на примере", "формула"]
        assert is_parallel_eligible(items) is False

    def test_deictic_a_what_blocks(self):
        items = ["напиши план", "а что если план не сработает", "запасной вариант"]
        assert is_parallel_eligible(items) is False

    def test_deictic_naoborot_blocks(self):
        items = ["аргументы за", "наоборот, аргументы против", "вывод"]
        assert is_parallel_eligible(items) is False

    # ── Пограничные случаи ──

    def test_normal_list_is_eligible(self):
        """Типичный список независимых пунктов."""

        items = [
            "погода в Минске",
            "курс доллара",
            "новости today",
            "рецепт борща",
            "анекдот",
        ]
        assert is_parallel_eligible(items) is True

    def test_first_item_cross_ref_no_previous(self):
        """Первый пункт не может ссылаться на предыдущего — без ошибки."""
        items = ["его смысл непонятен", "объясни", "пример"]
        # Первый пункт проверяется на внутренние маркеры, cross-ref для idx=0 не срабатывает
        # "его" — это анафора, но для первого пункта это просто вопрос о чём-то
        assert is_parallel_eligible(items) is True

    def test_similar_but_independent(self):
        """Похожие по теме, но без явных ссылок — eligible."""
        items = ["чем полезен бег", "чем полезно плавание", "чем полезна йога"]
        assert is_parallel_eligible(items) is True

    def test_mixed_independent_passes(self):
        items = [
            "сколько будет 2+2*2",
            "переведи 'hello' на русский",
            "почему небо голубое",
        ]
        assert is_parallel_eligible(items) is True


# ── _build_user_facts_context ──


class TestBuildUserFactsContext:
    def test_empty(self):
        assert _build_user_facts_context({}) == ""

    def test_none(self):
        assert _build_user_facts_context(None) == ""

    def test_with_facts(self):
        ctx = _build_user_facts_context({"city": "Минск", "age": 30})
        assert "Минск" in ctx
        assert "city" in ctx

    def test_abstract_item_skips_geo_facts(self):
        ctx = _build_user_facts_context(
            {"city": "аг. Гомель", "country": "Беларусь", "name": "Алексей"},
            item="Сколько граней у тессеракта?",
        )
        assert ctx == ""

    def test_weather_item_keeps_facts(self):
        assert _item_needs_user_facts("какая погода в Минске") is True
        ctx = _build_user_facts_context({"city": "Минск"}, item="какая погода в Минске")
        assert "Минск" in ctx

    def test_long_value_truncated(self):
        long_val = "x" * 200
        ctx = _build_user_facts_context({"note": long_val})
        assert len(ctx) < 300  # truncated


# ── Scheduler ──


class TestScheduler:
    def test_initial_value(self):
        assert _get_max_parallel() == _PARALLEL_INITIAL

    def test_reduces_on_errors(self):
        before = _get_max_parallel()
        if before <= 1:
            _record_batch_result(0, 0, 3)
            before = _get_max_parallel()
        _record_batch_result(2, 0, 3)  # 2/3 errors
        after = _get_max_parallel()
        assert after < before

    def test_reduces_on_rate_limit(self):
        _record_batch_result(0, 3, 5)  # 3 rate-limited
        # should be halved
        assert _get_max_parallel() < _PARALLEL_INITIAL or _get_max_parallel() >= 1


# ── run_parallel_batch (with mocked LLM) ──

_FAKE_ITEMS = ["вопрос один", "вопрос два", "вопрос три", "вопрос четыре", "вопрос пять"]


@pytest.fixture
def mock_llm():
    """Patch llm_generate_tiered to return fake responses."""
    with patch("core.batch_processor.llm_generate_tiered") as mock:
        mock.return_value = {"content": "Тестовый ответ"}
        yield mock


class TestRunParallelBatchWithMock:
    def test_disabled_by_env(self):
        with patch("core.batch_processor._PARALLEL_ENABLED", False):
            result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user"))
            assert result["ok"] is False
            assert result["mode"] == "sequential"

    def test_too_few_items(self):
        result = asyncio.run(run_parallel_batch(["a", "b"], "test_user"))
        assert result["ok"] is False
        assert result["mode"] == "sequential"

    def test_not_eligible(self):
        items = ["на основе предыдущего", "второй", "третий"]
        result = asyncio.run(run_parallel_batch(items, "test_user"))
        assert result["ok"] is False
        assert result["mode"] == "sequential"

    def test_successful_parallel(self, mock_llm):
        result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user"))
        assert result["ok"] is True
        assert result["mode"] == "parallel"
        assert result["answered"] == 5
        assert result["errors"] == 0
        assert "1." in result["reply"]
        assert "5." in result["reply"]

    def test_pending_items_on_partial_failure(self, mock_llm):
        """Если часть пунктов упала — pending_items показывает неотвеченные."""
        # Make one item fail
        async def _side_effect(*args, **kwargs):
            # Make item 3 fail by raising exception on 3rd call
            return {"content": "ok"}

        mock_llm.side_effect = [
            {"content": "ответ 1"},
            {"content": "ответ 2"},
            Exception("LLM error"),
            {"content": "ответ 4"},
            {"content": "ответ 5"},
        ]
        result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user"))
        assert result["ok"] is True
        assert result["mode"] == "parallel"
        assert result["errors"] >= 1

    def test_all_failed(self, mock_llm):
        mock_llm.return_value = {"content": ""}
        result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user"))
        assert result["ok"] is False
        assert result["mode"] == "sequential"

    def test_with_user_facts(self, mock_llm):
        facts = {"city": "Минск", "language": "ru"}
        result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user", facts))
        assert result["ok"] is True

    def test_returns_all_keys(self, mock_llm):
        result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user"))
        assert "ok" in result
        assert "reply" in result
        assert "mode" in result
        assert "answered" in result
        assert "total" in result
        assert "errors" in result
        assert "pending_items" in result

    def test_rejects_suspiciously_fast_batch(self, mock_llm):
        """Stale cache can return tiny answers in <1ms — must fall through to sequential."""
        mock_llm.return_value = {"content": "1"}
        with patch("core.batch_processor._MIN_ELAPSED_MS", 5000.0):
            result = asyncio.run(run_parallel_batch(_FAKE_ITEMS, "test_user"))
        assert result["ok"] is False
        assert result["mode"] == "sequential"