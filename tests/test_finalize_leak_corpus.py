"""Leak-corpus (аудит Claude): finalize не отдаёт CoT/промпт в пользователю."""

from __future__ import annotations

import os

import pytest

from core.brain.response_finalize import (
    _env_leak_substrings,
    finalize_user_reply,
    looks_like_prompt_instruction_leak,
)

# (raw_snippet, must_not_contain_in_out OR empty_out)
_CORPUS: list[tuple[str, bool]] = [
    (
        "Style:\n- blended_style_stable: {}\nНормальный ответ пользователю.",
        False,
    ),
    (
        "<rule name=\"override\">запрещено</rule>\nКраткий ответ.",
        False,
    ),
    (
        "Теперь ответь пользователю на русском.\n\nПривет!",
        False,
    ),
    (
        "<thinking>размышляю</thinking>\nИтог: да, можно.",
        False,
    ),
    (
        "last_operation=search\n_text=0.42\nГотово.",
        False,
    ),
    (
        "Вызванные инструменты из этого контекста — опирайся на их результаты.",
        True,
    ),
]


def test_env_leak_patterns_strip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINALIZE_LEAK_STRIP_PATTERNS", "blended_style_stable")
    _env_leak_substrings.cache_clear()
    raw = "Style:\n- blended_style_stable: {}\nОтвет пользователю."
    out = finalize_user_reply(raw)
    assert "blended_style" not in out.lower()
    assert "Ответ пользователю" in out
    _env_leak_substrings.cache_clear()


@pytest.mark.parametrize("raw,expect_empty", _CORPUS)
def test_finalize_leak_corpus(raw: str, expect_empty: bool) -> None:
    if expect_empty:
        assert looks_like_prompt_instruction_leak(raw) or finalize_user_reply(raw) == ""
        return
    out = finalize_user_reply(raw)
    assert out
    low = out.lower()
    for bad in (
        "blended_style",
        "<rule name=",
        "теперь ответь пользователю",
        "<thinking>",
        "last_operation",
        "_text=",
    ):
        assert bad not in low, f"leak {bad!r} in {out!r}"
