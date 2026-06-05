"""
Эвристика «глубины» задачи: один вопрос vs многоуровневый запрос.
Влияет на выбор короткого/длинного пути в подсказках и опциональный LLM-контур.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from core.dialogue_plot_signals import plot_twist_likely

Tier = Literal["shallow", "nested", "deep"]

_TIER_RANK = {"shallow": 0, "nested": 1, "deep": 2}

_NUM_START = re.compile(r"^\s*\d+[\).]", re.MULTILINE)
_MULTI_Q = re.compile(r"\?.*\?")

_SCENARIO_MARKERS = (
    "сценар",
    "ветк",
    "риск",
    "последств",
    "если бы ",
    "а если ",
    "что если",
    "what if",
    "scenario",
    "branch",
    "trade-off",
    "компромис",
)

# Неопределённость / условия / сроки не ясны → режим сценарного контура (nested/deep), не только по длине текста.
_UNCERTAINTY_STRONG = (
    "неизвестно",
    "неизвестен",
    "неясно",
    "не определ",
    "неопредел",
    "нет данных",
    "нет точн",
    "срок не",
    "неизвестен срок",
    "пока неизвест",
    "техработ",
    "ограничил",
    "ограничива",
    "ограничения ",
    "ограничение ",
    "заблокиров",
    "может не сработ",
    "может не прой",
    "риск ",
    "риском",
    "риски",
    "восстановят",
    "когда снимут",
    "без регламент",
    "unknown",
    "uncertain",
    "unclear",
    "maintenance",
    "restricted",
    "tbd",
    "if unsure",
)
_UNCERTAINTY_BRANCH = (
    "возможно ",
    "может быть",
    "вдруг ",
    "а если ",
    "что если",
    "если не ",
    "если всё ",
    "если все ",
    "in case ",
    "what if",
)
_TIME_PRESSURE = re.compile(
    r"(?:через\s+\d+\s*(?:дн|дня|день|недел|месяц)|к\s+отъезду|к\s+поездк|до\s+отъезда|deadline|к\s+сроку)",
    re.IGNORECASE,
)


def _uncertainty_boost_tier(text: str) -> Tier | None:
    """Короткий текст с маркерами неопределённости → nested; накопление → deep."""
    raw = (text or "").strip()
    if len(raw) < 12:
        return None
    low = raw.lower()
    score = 0
    for m in _UNCERTAINTY_STRONG:
        if m in low:
            score += 2
    for m in _UNCERTAINTY_BRANCH:
        if m in low:
            score += 1
    if _TIME_PRESSURE.search(raw):
        score += 1
    # Одиночное сильное «если …» в длине (ветвление без простыни)
    if low.count(" если ") >= 2 or low.count(" if ") >= 2:
        score += 1
    if score >= 5:
        return "deep"
    if score >= 2:
        return "deep" if len(raw) > 500 else "nested"
    if score >= 1 and len(raw) >= 40:
        return "nested"
    return None


def apply_tier_ceiling(tier: str, ceiling: str | None) -> str:
    """Не поднимать глубину выше ceiling (например CDC: nested вместо deep)."""
    if not ceiling or str(ceiling).strip() not in _TIER_RANK:
        return (tier or "shallow").strip() or "shallow"
    t = (tier or "shallow").strip()
    if t not in _TIER_RANK:
        t = "shallow"
    c = str(ceiling).strip()
    if _TIER_RANK[t] > _TIER_RANK[c]:  # type: ignore[index]
        return c
    return t


def max_task_tier(*tiers: str) -> str:
    """Взять максимальную глубину из набора строковых tier."""
    best: Tier = "shallow"
    for raw in tiers:
        t = (raw or "").strip()
        if t not in _TIER_RANK:
            continue
        if _TIER_RANK[t] > _TIER_RANK[best]:
            best = t  # type: ignore[assignment]
    return best


def min_task_tier(*tiers: str) -> str:
    """Минимальная глубина из набора (для потолков по intent / predictive)."""
    best: Tier = "deep"
    for raw in tiers:
        t = (raw or "").strip()
        if t not in _TIER_RANK:
            continue
        if _TIER_RANK[t] < _TIER_RANK[best]:
            best = t  # type: ignore[assignment]
    return best


def _weak_continuation_utterance(text: str) -> bool:
    """Короткая реплика «продолжи» без новой темы — не тянуть deep только из длинной истории."""
    t = (text or "").strip()
    if not t or len(t) > 160:
        return False
    low = t.lower().replace("ё", "е")
    if low in {"ок", "ок.", "давай", "дальше", "далее", "продолжай", "продолжи", "next", "go on"}:
        return True
    starters = (
        "продолж",
        "дальше",
        "далее",
        "ещё ",
        "еще ",
        "next ",
        "go on",
        "давай ",
        "вперед",
    )
    return any(low.startswith(s) for s in starters)


def apply_task_tier_hysteresis(computed: str, previous: str | None) -> str:
    """
    Не обрубать глубину больше чем на один уровень за один ход пользователя:
    deep → shallow за одну короткую реплику превращается в nested.
    Повышение tier не ограничиваем.
    """
    c = (computed or "shallow").strip()
    if c not in _TIER_RANK:
        c = "shallow"
    p = (previous or "").strip()
    if p not in _TIER_RANK:
        return c
    cr, pr = _TIER_RANK[c], _TIER_RANK[p]  # type: ignore[index]
    if pr > cr:
        nr = max(cr, pr - 1)
        _rank_to_tier = ("shallow", "nested", "deep")
        return _rank_to_tier[nr]
    return c


def _apply_intent_and_signal_ceilings(
    tier: str,
    *,
    user_text: str,
    planned_intent: str | None,
    terse_mode: bool,
) -> str:
    m = (tier or "shallow").strip()
    if m not in _TIER_RANK:
        m = "shallow"
    raw = (user_text or "").strip()
    intent = (planned_intent or "").strip().lower()
    if intent == "math" and len(raw) < 520 and raw.count("?") <= 1:
        m = min_task_tier(m, "nested")
    if terse_mode and len(raw) < 280:
        m = min_task_tier(m, "nested")
    return m


def infer_task_tier(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "shallow"
    if plot_twist_likely(t):
        return "nested"
    low = t.lower()
    if len(t) > 900:
        return "deep"
    q_count = t.count("?")
    if q_count >= 3 or (q_count >= 2 and len(t) > 120):
        return "deep"
    if q_count >= 2 or _MULTI_Q.search(t):
        return "nested"
    if len(list(_NUM_START.finditer(t))) >= 2:
        return "deep"
    if any(
        phrase in low
        for phrase in (
            "сначала ",
            "потом ",
            "затем ",
            "во-первых",
            "во-вторых",
            "first ",
            "then ",
            "step 1",
            "шаг 1",
            "этап 1",
            "многоуровн",
            "несколько вопрос",
            "два вопроса",
            "три вопроса",
        )
    ):
        return "nested" if len(t) < 350 else "deep"
    if " и ещё " in low or " а также " in low or " plus " in low:
        return "nested"
    if any(m in low for m in _SCENARIO_MARKERS) and len(t) >= 20:
        return "nested" if len(t) < 420 else "deep"
    u = _uncertainty_boost_tier(t)
    if u:
        return u
    return "shallow"


def infer_task_tier_with_history(
    user_text: str,
    recent_dialogue: Any,
    *,
    max_user_turns: int = 4,
    previous_tier: str | None = None,
    planned_intent: str | None = None,
    terse_mode: bool = False,
) -> str:
    """
    Учитывает последние реплики пользователя: короткое «продолжи» не глубокое,
    но накопленная нить из нескольких вопросов/шагов повышает tier.

    previous_tier — гистерезис от прошлого хода (из dialogue_state).
    planned_intent / terse_mode — мягкие потолки, чтобы не уводить простые запросы в deep.
    """
    cur = infer_task_tier(user_text)
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        out = _apply_intent_and_signal_ceilings(
            cur, user_text=user_text, planned_intent=planned_intent, terse_mode=terse_mode
        )
        return apply_task_tier_hysteresis(out, previous_tier)
    n = max(1, min(8, int(max_user_turns)))
    user_chunks: list[str] = []
    for row in recent_dialogue:
        if not isinstance(row, dict) or str(row.get("role") or "") != "user":
            continue
        ut = str(row.get("text") or "").strip()
        if ut:
            user_chunks.append(ut)
    user_chunks = user_chunks[-n:]
    if not user_chunks:
        out = _apply_intent_and_signal_ceilings(
            cur, user_text=user_text, planned_intent=planned_intent, terse_mode=terse_mode
        )
        return apply_task_tier_hysteresis(out, previous_tier)
    blob = "\n".join(user_chunks)
    if (user_text or "").strip() and user_chunks[-1].strip() != (user_text or "").strip():
        blob = blob + "\n" + (user_text or "").strip()
    hist_tier = infer_task_tier(blob)
    if _weak_continuation_utterance(user_text) and cur == "shallow":
        hist_tier = min_task_tier(hist_tier, "nested")
    merged = max_task_tier(cur, hist_tier)
    merged = _apply_intent_and_signal_ceilings(
        merged, user_text=user_text, planned_intent=planned_intent, terse_mode=terse_mode
    )
    return apply_task_tier_hysteresis(merged, previous_tier)


def refine_task_tier_from_outline(tier: str, outline: Any) -> str:
    """После LLM outline: depth=multi / thorough / сценарии → не ниже nested при необходимости."""
    if not isinstance(outline, dict) or not outline:
        return tier
    t = tier
    depth = str(outline.get("depth") or "").strip().lower()
    prefer = str(outline.get("prefer") or "").strip().lower()
    scenarios = outline.get("scenarios")
    has_scen = isinstance(scenarios, list) and any(scenarios)
    subgoals = outline.get("subgoals")
    n_sub = len(subgoals) if isinstance(subgoals, list) else 0
    if depth == "multi":
        t = max_task_tier(t, "nested")
    if prefer == "thorough":
        t = max_task_tier(t, "nested")
    if has_scen:
        t = max_task_tier(t, "nested")
    if depth == "multi" and prefer == "thorough" and n_sub >= 3:
        t = max_task_tier(t, "deep")
    return t


def tier_prefers_thorough(tier: str) -> bool:
    return tier in ("nested", "deep")
