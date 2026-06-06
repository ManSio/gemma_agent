"""
Универсальный quality-check ответов перед отправкой пользователю.

Цель: ловить «пустые» шаблоны, в которых нет конкретики (никакого числа,
итога, конкретного утверждения). Это первый защитный слой против ответов
вида «Strict Solve Mode: Шаг 1/2/3/4. Делаем самопроверку», когда модуль
не смог реально решить задачу.

Используется и в strict-решателях (school_assistant), и в reasoning-конвейере.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

_GENERIC_PHRASES = (
    "фиксируем дано",
    "выбираем формулу/метод",
    "пошагово получаем результат",
    "делаем самопроверку",
    "что требуется найти",
)

_META_TUTOR_PHRASES = (
    "strict solve mode",
    "шаг 1",
    "алгоритм решения",
    "как решать",
    "если хочешь, скажи мне фразу",
)

_RESULT_MARKERS = (
    "итог:",
    "ответ:",
    "результат:",
    "вывод:",
    "x =",
    "x=",
    "верно",
    "неверно",
)

_NUMBER_RE = re.compile(r"-?\d+(?:[\.,]\d+)?")
_LATIN_OR_CYR_WORD_RE = re.compile(r"[A-Za-zА-Яа-я]{3,}")


def has_concrete_answer(text: str) -> bool:
    """True если в ответе есть явный итог/число/маркер результата."""
    if not text:
        return False
    low = text.lower()
    if any(m in low for m in _RESULT_MARKERS):
        return True
    # Минимум одно число + одно содержательное слово (помимо «шаг N»).
    has_num = bool(_NUMBER_RE.search(text))
    words = [w for w in _LATIN_OR_CYR_WORD_RE.findall(text) if w.lower() != "шаг"]
    return has_num and len(words) >= 4


def is_generic_template(text: str) -> bool:
    """True если ответ — известный пустой шаблон без конкретики."""
    if not text:
        return False
    low = text.lower()
    hits = sum(1 for ph in _GENERIC_PHRASES if ph in low)
    if hits >= 2 and not any(m in low for m in _RESULT_MARKERS):
        return True
    return False


def looks_like_help_dump(text: str) -> bool:
    """True если ответ — список slash-команд / справка вместо решения.

    Используется как анти-паттерн: модуль вместо ответа вернул /help.
    """
    if not text:
        return False
    t = text.strip()
    low = t.lower()
    # «Команды:\n/explain ...\n/solve ...\n/check ...\n/quiz ...» и подобные.
    bullet_slashes = sum(1 for line in t.splitlines() if line.strip().startswith("/"))
    if bullet_slashes >= 3 and (
        "/explain" in low
        or "/solve" in low
        or "/check" in low
        or "/quiz" in low
        or "использование:" in low
    ):
        return True
    return False


def has_meta_tutor_text(text: str) -> bool:
    """True если ответ уходит в мета-режим методички вместо решения."""
    if not text:
        return False
    low = str(text).strip().lower()
    if not low:
        return False
    return any(ph in low for ph in _META_TUTOR_PHRASES)


def quality_verdict(text: str) -> Tuple[bool, str]:
    """
    Вернуть (ok, reason). ok=False означает «лучше не отправлять как есть».
    """
    if not (text or "").strip():
        return False, "empty"
    if looks_like_help_dump(text):
        return False, "help_dump"
    if has_meta_tutor_text(text):
        return False, "meta_tutor_text"
    if is_generic_template(text):
        return False, "generic_template"
    if not has_concrete_answer(text):
        # Содержательный, но без явного итога — допускаем (анализ/уточнение).
        return True, "no_explicit_result"
    return True, "ok"


__all__ = [
    "has_concrete_answer",
    "is_generic_template",
    "looks_like_help_dump",
    "has_meta_tutor_text",
    "quality_verdict",
]
