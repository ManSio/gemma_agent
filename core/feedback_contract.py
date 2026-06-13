"""Контракт 👎/pending: уроки и hints по anchor нити, не по тексту эллипсиса."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _row_role(row: Dict[str, Any]) -> str:
    return str(row.get("role") or row.get("author") or "").lower()


def _row_text(row: Dict[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or "").strip()


def resolve_anchor_user_q(
    behavior_rec: Optional[Dict[str, Any]],
    rated_user_text: str,
) -> str:
    """Исходный вопрос нити для урока — предыдущий user turn, если рейтинг на уточнении."""
    rated = (rated_user_text or "").strip()
    rec = behavior_rec if isinstance(behavior_rec, dict) else {}
    rd = rec.get("recent_messages") or rec.get("recent_dialogue")
    if isinstance(rd, list) and rated:
        users: List[str] = []
        for row in rd:
            if not isinstance(row, dict):
                continue
            if _row_role(row) in ("user", "human", ""):
                t = _row_text(row)
                if t:
                    users.append(t)
        if len(users) >= 2 and users[-1].strip().lower() == rated.lower():
            prev = users[-2].strip()
            if len(prev) >= 8:
                return prev[:280]
    try:
        from core.dialogue_recheck_anchor import last_substantive_user_question

        if isinstance(rd, list):
            q = last_substantive_user_question(rd, skip_current=True, min_len=8)
            if q and q.strip().lower() != rated.lower():
                return q.strip()[:280]
    except Exception as e:
        logger.debug("resolve_anchor_user_q: %s", e)
    return rated[:280]


def rating_failure_class(
    rated_user_text: str,
    anchor_user_q: str,
    *,
    intent: str = "",
) -> str:
    """Класс ошибки для метаданных урока (без keyword-списков тем)."""
    rated = (rated_user_text or "").strip()
    anchor = (anchor_user_q or "").strip()
    if anchor and rated.lower() != anchor.lower():
        try:
            from core.brain.discourse_resolver import _immediate_thread_followup

            if _immediate_thread_followup(rated, {"recent_dialogue": [], "session_task": {"last_outcome": "ok"}}):
                pass
        except Exception:
            pass
        if len(rated) <= 56 and len(rated.split()) <= 8:
            return "thread_followup_drift"
    if str(intent or "").strip().lower() in ("explain", "general"):
        return "reply_quality"
    return "generic_rating"


def rating_lesson_trigger(
    *,
    rated_user_text: str,
    anchor_user_q: str,
) -> Tuple[str, bool]:
    """Триггер урока: anchor нити, не эллипсис «почему так»."""
    rated = (rated_user_text or "").strip()
    anchor = (anchor_user_q or "").strip()
    if anchor and anchor.lower() != rated.lower() and len(anchor) >= 12:
        chunk = re.sub(r"\s+", " ", anchor)[:72].strip()
        return chunk[:48], False
    from core.user_correction_bus import lesson_trigger_from_user_text

    return lesson_trigger_from_user_text(rated)


def rating_lesson_instruction(
    *,
    rated_user_text: str,
    anchor_user_q: str,
    intent: str,
    module: str,
    correction_text: str,
    failure_class: str,
) -> str:
    """Инструкция урока с привязкой к anchor, без generic meta."""
    anchor = (anchor_user_q or "").strip()
    rated = (rated_user_text or "").strip()
    corr = (correction_text or "").strip()
    if corr:
        return corr[:500]
    if failure_class == "thread_followup_drift" and anchor:
        return (
            f"На исходный вопрос «{anchor[:180]}» и короткие уточнения к нему — "
            "отвечай по теме этого вопроса. Не уходи в meta про ограничения LLM/агента "
            "и не перечисляй общие возможности модели."
        )[:500]
    from core.user_correction_bus import negative_rating_lesson_instruction

    return negative_rating_lesson_instruction(
        user_text=rated,
        intent=intent,
        module=module,
        correction_text=correction_text,
    )


def build_rating_lesson_meta(
    *,
    user_id: str,
    trace_id: str,
    anchor_user_q: str,
    failure_class: str,
    source: str,
    behavior_rec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Метаданные ephemeral lesson для фильтрации по нити/эпохе."""
    rec = behavior_rec if isinstance(behavior_rec, dict) else {}
    epoch = rec.get("conversation_epoch")
    epoch_id = int(epoch.get("id") or 0) if isinstance(epoch, dict) else 0
    return {
        "source": source,
        "user_id": str(user_id or ""),
        "trace_id": str(trace_id or "")[:64],
        "anchor_user_q": (anchor_user_q or "")[:280],
        "failure_class": failure_class,
        "epoch_id": epoch_id,
    }


def _active_anchor_from_context(context: Optional[Dict[str, Any]]) -> str:
    """Активный anchor нити из discourse / session."""
    ctx = context if isinstance(context, dict) else {}
    dr = ctx.get("discourse_resolution")
    if isinstance(dr, dict):
        lq = str(dr.get("last_user_q") or "").strip()
        if lq:
            return lq[:280]
    tm = ctx.get("turn_meaning")
    if isinstance(tm, dict):
        resolved = str(tm.get("resolved_user_text") or "").strip()
        if resolved:
            return resolved[:280]
    st = ctx.get("session_task")
    if isinstance(st, dict):
        ex = str(st.get("last_user_excerpt") or "").strip()
        if ex:
            return ex[:280]
    return ""


def _anchor_thread_overlap(anchor: str, active: str) -> bool:
    """Совпадение anchor урока с активной нитью."""
    a = (anchor or "").strip()
    b = (active or "").strip()
    if not a or not b:
        return False
    al, bl = a[:48].lower(), b[:48].lower()
    if al in bl or bl in al:
        return True
    try:
        from core.brain.discourse_resolver import _thread_content_tokens

        return bool(_thread_content_tokens(a, min_len=4) & _thread_content_tokens(b, min_len=4))
    except Exception:
        return False


def collect_lessons_for_context(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Уроки по тексту хода + по anchor активной нити (для эллипсиса)."""
    from core.ephemeral_lessons import load_document, match_lessons

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for le in match_lessons(user_text or ""):
        lid = str(le.get("id") or "")
        key = lid or str(le.get("trigger") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(le)
    active = _active_anchor_from_context(context)
    if not active:
        return out
    doc = load_document()
    for le in doc.get("lessons") or []:
        if not isinstance(le, dict) or not le.get("active", True):
            continue
        meta = le.get("meta") if isinstance(le.get("meta"), dict) else {}
        anchor = str(meta.get("anchor_user_q") or "").strip()
        if not anchor or not _anchor_thread_overlap(anchor, active):
            continue
        lid = str(le.get("id") or "")
        key = lid or str(le.get("trigger") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(le)
    return out


def lesson_applies_in_context(
    lesson: Dict[str, Any],
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Применять ли урок в текущем ходе (фильтр legacy generic 👎)."""
    if not isinstance(lesson, dict):
        return False
    meta = lesson.get("meta") if isinstance(lesson.get("meta"), dict) else {}
    anchor = str(meta.get("anchor_user_q") or "").strip()
    ctx = context if isinstance(context, dict) else {}
    if anchor:
        active = _active_anchor_from_context(ctx)
        if not active:
            return False
        return _anchor_thread_overlap(anchor, active)
    inst = str(lesson.get("instruction") or "")
    if "исправь подход" in inst.lower() and ctx:
        try:
            from core.brain.discourse_resolver import _immediate_thread_followup

            if _immediate_thread_followup(user_text, ctx):
                return False
        except Exception as e:
            logger.debug("lesson_applies immediate: %s", e)
    epoch_id = int(meta.get("epoch_id") or 0)
    if epoch_id:
        rec_epoch = ctx.get("conversation_epoch")
        cur = int(rec_epoch.get("id") or 0) if isinstance(rec_epoch, dict) else 0
        if cur and cur != epoch_id:
            return False
    return True


def brain_addon_for_context(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Ephemeral lessons с фильтром по контракту нити."""
    ms = collect_lessons_for_context(user_text or "", context)
    if not ms:
        return ""
    lines: List[str] = []
    for le in ms:
        if not lesson_applies_in_context(le, user_text, context):
            continue
        inst = str(le.get("instruction") or "").strip()
        if inst:
            lines.append(inst)
    if not lines:
        return ""
    return "Временные правки оператора (до правки кода; строго соблюдай):\n" + "\n".join(
        f"- {ln}" for ln in lines
    )
