"""
Общие эвристики для Goal Runner: определение типа задачи.

Используется как goal_runner.py, так и brain/goal_runner_nudge для
согласованного определения multi-step / pure-text / tool-required.

Политика «двух дорожек»:
- Pure-text task → обычный ответ без Goal Runner (аналитика, описание, план на словах)
- Multi-step с инструментами → Goal Runner
- Неопределённые → Goal Runner только по /goal_run
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

# ── Признаки чисто текстовой задачи (без инструментов) ──────────────
# Аналитика, рефлексия, объяснение, план на словах — не требует поиска

_TEXT_ONLY_MARKERS: Tuple[re.Pattern, ...] = (
    # Аналитические и рефлексивные запросы
    re.compile(r"(?i)\bоцен(и|к)\w*\s+(ситуаци|план|риск|шанс|вероятност)"),
    re.compile(r"(?i)\bпроанализируй\b"),
    re.compile(r"(?i)\bперечисли\b"),
    re.compile(r"(?i)\bранжируй\b"),
    re.compile(r"(?i)\bранжир(уй|овать)"),
    re.compile(r"(?i)\bопиши\s+план\b"),
    re.compile(r"(?i)\bчестно\s+оцен"),
    re.compile(r"(?i)\bсамы[ея]\s+слабых?\s+мест"),
    re.compile(r"(?i)\bальтернативн\w+\s+действи"),
    re.compile(r"(?i)\bпо\s+критериям?\b"),
    re.compile(r"(?i)\b(реалистичность|робастность|эффективность)"),
    re.compile(r"(?i)\bмысл\w+\s+логическ"),
    re.compile(r"(?i)\bне\s+использ(уй|уйте)\s+внешн"),
    re.compile(r"(?i)\bобъём\w*\s*[:]\s*\d+"),
    re.compile(r"(?i)\bбез\s+воды\b"),
    # Оценка / список / рейтинг
    re.compile(r"(?i)\bоцен(и|к)\w*\s+ситуаци"),
    re.compile(r"(?i)\bдай(те)?\s+рекомендац"),
    re.compile(r"(?i)\bглавн\w+\s+(угроз|проблем|риск)"),
    re.compile(r"(?i)\bнеизвестн\w+\s+фактор"),
    # Запрос на генерацию идей / текста
    re.compile(r"(?i)\bсгенерируй\s+(идею|текст|статью)"),
    re.compile(r"(?i)\bнапиши\s+(рассказ|эссе|статью|сочинение)"),
    re.compile(r"(?i)\bпридумай\b"),
    re.compile(r"(?i)\bвообрази\b"),
    # Симуляция / ролевая игра
    re.compile(r"(?i)\bты\s+(находишься|попал|оказался)\b"),
    re.compile(r"(?i)\bты\s+[—\-]\s+(агент|робот|человек)"),
    re.compile(r"(?i)\bпредставь\s+ситуаци"),
    re.compile(r"(?i)\bзадание\s+для\s+агента"),
    re.compile(r"(?i)\bспасени\w+\s+в\s+экстремальн"),
    # План действий (без инструментов)
    re.compile(r"(?i)\b(долгосрочн|краткосрочн|стратегическ)\w*\s+стратег"),
    re.compile(r"(?i)\bплан\s+на\s+следующ"),
    re.compile(r"(?i)\bплан\s+действ"),
)

_TOOL_REQUIRED_MARKERS: Tuple[re.Pattern, ...] = (
    # Поиск / сбор данных
    re.compile(r"(?i)\bнайди\s+(информаци|данн|стать|сайт|источник)"),
    re.compile(r"(?i)\bпоищи\b"),
    re.compile(r"(?i)\bпроверь?\s+(ссылку|URL|сайт)"),
    re.compile(r"(?i)\bскачай\b"),
    re.compile(r"(?i)\bзагрузи\b"),
    re.compile(r"(?i)\bпроскан\w+\s+(сайт|URL|страниц)"),
    # Явное упоминание сайтов / источников как целей
    re.compile(r"(?i)\bс\s+(сайт\w*|источник\w*)\b"),
    re.compile(r"(?i)\bна\s+сайт\w*\b"),
    # Сравнение с контекстом поиска
    re.compile(r"(?i)\bсобер\w*\s+(информац|данн|факт)"),
    re.compile(r"(?i)\bсравни\b.+\b(цен|стоимост|характеристик)"),
    # Код / технические действия
    re.compile(r"(?i)\bнапиши\s+(код|программ|функци)"),
    re.compile(r"(?i)\bсоздай?\s+(проект|файл|модуль)"),
    re.compile(r"(?i)\bреализуй\b"),
    re.compile(r"(?i)\bотрефактор\w+\b"),
    re.compile(r"(?i)\bразверн\w+\b"),
    re.compile(r"(?i)\bдеплой\b"),
    # Многошаговость (нужны инструменты)
    re.compile(r"(?i)\bвыполни\s+(по\s+шагам|шаги)"),
    re.compile(r"(?i)\bсделай\s+до\s+конца\b"),
)


def _min_text_chars() -> int:
    """Минимум символов для классификации pure-text."""
    try:
        return max(16, int(os.getenv("GOAL_RUNNER_PURE_TEXT_MIN_CHARS", "40")))
    except ValueError:
        return 40


def is_pure_text_task(user_text: str) -> bool:
    """
    True — задача решается только текстом, без инструментов.
    Goal Runner не нужен.
    """
    t = (user_text or "").strip()
    if len(t) < _min_text_chars():
        return False
    low = t.lower()

    # Если есть явные признаки необходимости инструментов — не pure-text
    for pat in _TOOL_REQUIRED_MARKERS:
        if pat.search(low):
            return False

    # Проверяем признаки чисто текстовой задачи
    for pat in _TEXT_ONLY_MARKERS:
        if pat.search(low):
            return True

    return False


def _multistep_min_chars() -> int:
    try:
        return max(16, min(120, int(os.getenv("GOAL_RUNNER_MULTISTEP_MIN_CHARS", "24"))))
    except ValueError:
        return 24


# ── Явные признаки многошаговости (копия из goal_runner_nudge) ─────

_MULTI_STEP_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"(?m)^\s*\d+[\.\)]\s+\S"),
    re.compile(r"(?i)\bсначал\w*.+\bпотом\b"),
    re.compile(r"(?i)\bсначал\w*.+\bзатем\b"),
    re.compile(r"(?i)\bшаг\s*\d"),
    re.compile(r"(?i)по\s+шагам"),
    re.compile(r"(?i)многошагов"),
    re.compile(r"(?i)\bэтап\w*\s*\d"),
    re.compile(r"(?i)состав\w*\s+план\s+и\s+выполни"),
    re.compile(r"(?i)сделай\s+до\s+конца"),
    re.compile(r"(?i)автоматически\s+выполни"),
    re.compile(r"(?i)\bвыполни\s+вс[её]\s+следующ"),
    re.compile(r"(?i)\bсравни\b.+\bисточник"),
    re.compile(r"(?i)\bсравни\b.+\b(верси|мнен|статей|стать)"),
    re.compile(r"(?i)\b(несколько|три|два|двух|трёх|трех)\s+(источник|сайт|стат|вариант)"),
    re.compile(r"(?i)\bв\s+нескольк\w*\s+(источник|сайт|журнал|стат)"),
    re.compile(r"(?i)\bсобер\w*\s+(информац|данн|факт).+\b(сравни|оформи|выведи|сделай)"),
    re.compile(r"(?i)\bпошагово\b"),
    re.compile(r"(?i)\bподробн\w*\s+инструкц"),
    re.compile(r"(?i)\bалгоритм\s+(действ|выполн)"),
    re.compile(r"(?i)\bот\s+и\s+до\b"),
    re.compile(r"(?i)\bпроверь\b.+\bпотом\b"),
    re.compile(r"(?i)\bнайди\b.+\b(потом|затем|а\s+потом)\b"),
    # Найди X и сравни / сопоставь
    re.compile(r"(?i)\bнайди\b.+\bи\s+сравн"),
    # Сравнение цен / характеристик / вариантов
    re.compile(r"(?i)\bсравн\w*\s+(цены|вариант|характеристик|услови)"),
)


def warrants_multistep_goal_text(user_text: str) -> bool:
    """Текст похож на многошаговую цель."""
    t = (user_text or "").strip()
    if len(t) < _multistep_min_chars():
        return False
    if t.startswith("/"):
        return False
    low = t.lower()
    if "/goal_run" in low:
        return False
    return any(pat.search(t) for pat in _MULTI_STEP_PATTERNS)


# ── Определение типа задачи ──────────────────────────────────────────

class TaskType:
    """Тип задачи: pure_text | multistep_tool | multistep_text | simple"""
    PURE_TEXT = "pure_text"
    MULTISTEP_TOOL = "multistep_tool"
    MULTISTEP_TEXT = "multistep_text"
    SIMPLE = "simple"


def classify_goal_runner_need(
    user_text: str,
    *,
    tool_names: Optional[Set[str]] = None,
) -> str:
    """
    Классифицировать, нужен ли Goal Runner.

    Returns:
      TaskType.PURE_TEXT — только текст, не нужно
      TaskType.MULTISTEP_TOOL — многошагово с инструментами → Goal Runner
      TaskType.MULTISTEP_TEXT — многошагово, но только текст → не нужно
      TaskType.SIMPLE — простой запрос → не нужно
    """
    t = (user_text or "").strip()
    if not t:
        return TaskType.SIMPLE

    is_multi = warrants_multistep_goal_text(t)
    is_pure = is_pure_text_task(t)

    # Чисто текстовая задача (аналитика, симуляция, рефлексия)
    if is_pure:
        return TaskType.PURE_TEXT

    # Многошаговая — уточняем, нужны ли инструменты
    if is_multi:
        # Если есть явные признаки необходимости инструментов
        low = t.lower()
        for pat in _TOOL_REQUIRED_MARKERS:
            if pat.search(low):
                return TaskType.MULTISTEP_TOOL
        # Если нет признаков инструментов — это multistep_text (аналитика)
        return TaskType.MULTISTEP_TEXT

    return TaskType.SIMPLE


# ── Общие утилиты ────────────────────────────────────────────────────

def is_simple_question(user_text: str) -> bool:
    """Короткий вопрос без признаков многошаговости и инструментов."""
    t = (user_text or "").strip()
    if len(t) < _multistep_min_chars():
        return True
    if warrants_multistep_goal_text(t):
        return False
    if is_pure_text_task(t):
        return False
    low = t.lower()
    for pat in _TOOL_REQUIRED_MARKERS:
        if pat.search(low):
            return False
    # Проверяем, похож ли на вопрос
    if "?" in t or t.endswith("?") or t.endswith("?"):
        return True
    return False


__all__ = [
    "TaskType",
    "classify_goal_runner_need",
    "is_pure_text_task",
    "warrants_multistep_goal_text",
    "is_simple_question",
]
