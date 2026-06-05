"""
Подсказка мозгу: при явной многошаговости предложить /goal_run (если автостарт выкл).

Эвристики совпадают с автостартом Goal Runner (GOAL_RUNNER_AUTO_START, по умолчанию on).
Вкл. вместе с GOAL_RUNNER_ENABLED. Подсказка отдельно выкл: GOAL_RUNNER_BRAIN_NUDGE=false

Эвристики переиспользуются из core/goal_runner_types.py.
"""
from __future__ import annotations

import os

from core.goal_runner_types import warrants_multistep_goal_text as _warrants_text


def _goal_runner_enabled() -> bool:
    ex = (os.getenv("GOAL_RUNNER_EXECUTOR_MODE") or os.getenv("GOAL_RUNNER_ULTIMATE") or "").strip().lower()
    if ex in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("GOAL_RUNNER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _nudge_enabled() -> bool:
    raw = os.getenv("GOAL_RUNNER_BRAIN_NUDGE")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _auto_start_nl() -> bool:
    """Синхронно с core.goal_runner.auto_start_from_nl (без импорта цикла)."""
    if not _goal_runner_enabled():
        return False
    raw = os.getenv("GOAL_RUNNER_AUTO_START")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _multistep_min_chars() -> int:
    try:
        return max(16, min(120, int((os.getenv("GOAL_RUNNER_MULTISTEP_MIN_CHARS") or "24").strip() or "24")))
    except ValueError:
        return 24


def warrants_multistep_goal_text(user_text: str) -> bool:
    """Делегирует в core.goal_runner_types."""
    return _warrants_text(user_text)


def format_goal_runner_routing_addon(user_text: str) -> str:
    """Строка для хвоста tool_routing_hint или пусто."""
    if not _goal_runner_enabled() or not _nudge_enabled():
        return ""
    if _auto_start_nl():
        return ""
    if not warrants_multistep_goal_text(user_text):
        return ""
    return (
        "goal_runner_nudge: сообщение похоже на **многошаговую цель**. Если в каталоге есть **/goal_run** — в ответе "
        "**одним коротким предложением** предложи пользователю **`/goal_run …`** (его формулировка); "
        "параллельно можно сделать первый уместный TOOL_CALL, если это не мешает ясности."
    )
