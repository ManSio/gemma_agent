"""
Справочник ситуаций: на любой тип сообщения — lane (профиль brain) + подсказки + флаги.

Подключается из scenario_engine.forecast_pre_turn (не дублировать логику в orchestrator).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


@dataclass
class SituationEntry:
    id: str
    priority: int
    lane: str
    hints: List[str] = field(default_factory=list)
    suppress_fact_confirmation: bool = False
    prefer_news_direct: bool = False
    force_anti_intrusion: bool = False
    expect_multi_answer: bool = False


Matcher = Callable[[Any], bool]


def _low(ctx: Any) -> str:
    return (ctx.user_text or "").strip().lower()


def _m_equation(ctx: Any) -> bool:
    t = _low(ctx)
    return bool(re.search(r"(?i)уравнен|реши\s+.*=|2x|3x|\bx\s*=", t))


def _m_translation(ctx: Any) -> bool:
    try:
        from core.brain.translation_path import is_translation_turn

        return is_translation_turn(ctx.user_text or "")
    except Exception:
        return False


def _m_code(ctx: Any) -> bool:
    t = _low(ctx)
    return bool(
        re.search(
            r"(?i)напиши\s+(?:код|скрипт|функци)|"
            r"python|javascript|факториал|for\s+в\s+python|def\s+\w+",
            t,
        )
    )


def _m_continue(ctx: Any) -> bool:
    try:
        from core.brain.user_facing_contract import (
            classify_short_user_turn,
            is_short_turn_continuing_dialogue,
        )

        rd = None
        if getattr(ctx, "dialogue_state", None) and isinstance(ctx.dialogue_state, dict):
            rd = ctx.dialogue_state.get("recent_dialogue")
        kind = classify_short_user_turn(
            getattr(ctx, "user_text", "") or "",
            rd,
        )
        return is_short_turn_continuing_dialogue(kind) or kind == "continuation"
    except Exception:
        t = _low(ctx).strip()
        return t in {"продолжи", "продолжай", "дальше", "continue", "go on"}


def _m_geo(ctx: Any) -> bool:
    try:
        from core.geo_nearby_reply import is_geo_topic_context

        return is_geo_topic_context(
            getattr(ctx, "user_text", "") or "",
            has_location_attachment=bool(
                ctx.has_attachment and "location" in str(ctx.file_type or "")
            ),
        )
    except Exception:
        t = _low(ctx)
        if ctx.has_attachment and "location" in str(ctx.file_type or ""):
            return True
        return bool(re.search(r"(?i)геометк|координат|/geo_help", t))


def _m_math_calc(ctx: Any) -> bool:
    t = _low(ctx)
    if _m_equation(ctx):
        return False
    return bool(re.search(r"(?i)^\s*/calc\b|посчитай|вычисли|\d+\s*[\+\-\*/]", t))


def _m_explain(ctx: Any) -> bool:
    t = _low(ctx)
    return bool(re.search(r"(?i)объясни|поясни|как\s+работает|что\s+такое|расскажи\s+про", t))


def _m_teacher(ctx: Any) -> bool:
    t = _low(ctx)
    return bool(re.search(r"(?i)урок\s+по|present\s+perfect|грамматик|учебн", t))


def _m_chitchat(ctx: Any) -> bool:
    t = _low(ctx).strip()
    return len(t) <= 48 and bool(
        re.search(r"(?i)^(привет|здравств|добрый|как\s+дела|спасибо|пока|хай|hello)", t)
    )


def _m_image_only(ctx: Any) -> bool:
    return ctx.has_attachment and ctx.file_type == "image" and len((ctx.user_text or "").strip()) < 8


def _m_negative_feedback(ctx: Any) -> bool:
    try:
        from core.dialogue_feedback_signals import user_feedback_likely

        return user_feedback_likely(ctx.user_text or "")
    except Exception:
        return False


def _m_document(ctx: Any) -> bool:
    return ctx.file_type == "document" or bool(
        re.search(r"(?i)pdf|документ|файл|вложен", _low(ctx))
    )


PLAYBOOK: List[tuple[Matcher, SituationEntry]] = [
    (
        _m_negative_feedback,
        SituationEntry(
            id="situation_correction",
            priority=100,
            lane="standard",
            hints=["Реплика — исправление прошлого ответа; не продолжай старую ветку."],
            force_anti_intrusion=True,
        ),
    ),
    (
        _m_translation,
        SituationEntry(
            id="situation_translation",
            priority=95,
            lane="translation",
            hints=["Только перевод целевым языком, без tools и без мета-комментариев."],
        ),
    ),
    (
        _m_equation,
        SituationEntry(
            id="situation_equation",
            priority=94,
            lane="math_solve",
            hints=["Реши уравнение, выдай x=…; не подставляй только правую часть без x."],
        ),
    ),
    (
        _m_code,
        SituationEntry(
            id="situation_code",
            priority=93,
            lane="code_generation",
            hints=["Рабочий код + 1–2 строки как запустить; без утечки промпта и JSON."],
        ),
    ),
    (
        _m_continue,
        SituationEntry(
            id="situation_continue",
            priority=92,
            lane="quick_explain",
            hints=["Продолжи с места обрыва; не начинай тему заново; сохрани формат (код/список)."],
        ),
    ),
    (
        _m_image_only,
        SituationEntry(
            id="situation_image",
            priority=91,
            lane="document_qa",
            hints=["Опиши изображение или OCR; если не видно — честно скажи, не выдумывай."],
        ),
    ),
    (
        _m_teacher,
        SituationEntry(
            id="situation_teacher",
            priority=90,
            lane="tutorial",
            hints=["Объясни тему урока; не предлагай /solve заглушку school_assistant."],
        ),
    ),
    (
        _m_geo,
        SituationEntry(
            id="situation_geo",
            priority=85,
            lane="standard",
            hints=["Коротко по месту/рядом; без JSON tool schema в ответе."],
        ),
    ),
    (
        _m_math_calc,
        SituationEntry(
            id="situation_arithmetic",
            priority=84,
            lane="math_solve",
            hints=["Числовой результат с единицами; не путай с текстовой болтовнёй."],
        ),
    ),
    (
        _m_explain,
        SituationEntry(
            id="situation_explain",
            priority=80,
            lane="quick_explain",
            hints=["Сначала суть, потом детали; не обрывай код на полуслове."],
        ),
    ),
    (
        _m_document,
        SituationEntry(
            id="situation_document",
            priority=78,
            lane="document_qa",
            hints=["Опирайся на document_intake; не выдумывай содержимое файла."],
        ),
    ),
    (
        _m_chitchat,
        SituationEntry(
            id="situation_chitchat",
            priority=50,
            lane="short",
            hints=["Коротко и по делу; без лишних уточнений и сервисных кнопок."],
        ),
    ),
]


def playbook_hints_only_on_prose() -> bool:
    raw = os.getenv("SITUATION_PLAYBOOK_HINTS_ONLY_ON_PROSE", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def prose_blocks_playbook_lane(ctx: Any) -> bool:
    """Длинная prose — не менять situation_lane, только hints (B5)."""
    if not playbook_hints_only_on_prose():
        return False
    text = (getattr(ctx, "user_text", None) or "").strip()
    if not text:
        return False
    try:
        from core.heuristic_context_gate import _compute_prose_score, _prose_max_chars

        if len(text) > _prose_max_chars() + 60:
            return True
        if _compute_prose_score(text) >= 0.45:
            return True
    except Exception:
        if len(text) > 200:
            return True
    return False


def match_situation(ctx: Any) -> Optional[SituationEntry]:
    best: Optional[SituationEntry] = None
    for matcher, entry in PLAYBOOK:
        try:
            if matcher(ctx):
                if best is None or entry.priority > best.priority:
                    best = entry
        except Exception:
            continue
    return best


def apply_situation_to_forecast(
    fc: Any,
    entry: SituationEntry,
    ctx: Any = None,
) -> None:
    """Записывает lane/флаги в TurnForecast (duck typing)."""
    hints_only = bool(ctx is not None and prose_blocks_playbook_lane(ctx))
    if not hints_only:
        fc.situation_lane = entry.lane
    for h in entry.hints:
        if h not in fc.brain_hint_lines:
            fc.brain_hint_lines.append(h)
    if entry.suppress_fact_confirmation:
        fc.suppress_fact_confirmation = True
    if entry.prefer_news_direct:
        fc.prefer_news_direct = True
    if entry.force_anti_intrusion:
        fc.force_anti_intrusion = True
    if entry.expect_multi_answer:
        fc.expect_multi_answer_risk = True
