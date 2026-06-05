"""Оценка ответов: /rate, /correct, кнопки 👍/👎 → experience, CDC, уроки."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _feedback_log_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "user_feedback.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append_feedback_log(row: Dict[str, Any]) -> None:
    try:
        with open(_feedback_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("user_feedback log: %s", e)


def get_last_turn_context(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Последний маршрут из behavior session_task."""
    if not behavior_store or not user_id:
        return {}
    try:
        rec = behavior_store.load(str(user_id), group_id)
        st = rec.get("session_task") if isinstance(rec.get("session_task"), dict) else {}
        return dict(st)
    except Exception as e:
        logger.debug("get_last_turn_context: %s", e)
        return {}


def apply_user_rating(
    *,
    user_id: str,
    score: int,
    behavior_store: Any = None,
    group_id: Optional[str] = None,
    correction_text: str = "",
    source: str = "rate",
) -> Dict[str, Any]:
    """
    score: +1 хорошо, -1 плохо, 0 нейтрально (только лог).
    Возвращает сводку применённых действий.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return {"ok": False, "error": "empty user_id"}
    sc = max(-1, min(1, int(score)))
    ctx = get_last_turn_context(behavior_store, uid, group_id) if behavior_store else {}
    user_text = str(ctx.get("last_user_excerpt") or "")
    intent = str(ctx.get("last_intent") or "unknown")
    module = str(ctx.get("last_module") or "__fallback__")
    skill = str(ctx.get("last_skill") or ctx.get("skill_name") or "")
    assistant_excerpt = str(ctx.get("last_assistant_excerpt") or "")[:480]
    trace_id = str(ctx.get("last_trace_id") or "").strip()
    positive = sc > 0
    negative = sc < 0

    result: Dict[str, Any] = {
        "ok": True,
        "score": sc,
        "source": source,
        "intent": intent,
        "module": module,
        "skill": skill or None,
        "correction": (correction_text or "")[:400],
        "trace_id": trace_id[:64] if trace_id else None,
        "applied": [],
    }

    _append_feedback_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": uid,
            "score": sc,
            "source": source,
            "intent": intent,
            "module": module,
            "skill": skill or None,
            "correction": (correction_text or "")[:400],
            "user_excerpt": user_text[:200],
            "trace_id": trace_id[:64] if trace_id else None,
        }
    )
    try:
        from core.user_issue_journal import record_user_issue

        if sc != 0:
            record_user_issue(
                user_id=uid,
                score=sc,
                source=source,
                user_text=user_text,
                assistant_excerpt=assistant_excerpt,
                intent=intent,
                module=module,
                skill=skill,
                correction=correction_text,
                category="negative_rating" if negative else "positive_rating",
            )
    except Exception as e:
        logger.debug("user_issue_journal: %s", e)
    if negative and behavior_store:
        try:
            from core.dialogue_feedback_signals import merge_recent_remarks_into_routing_prefs

            rec = behavior_store.load(uid, group_id)
            rp = dict(rec.get("routing_prefs") or {})
            remark = (correction_text or user_text or "оценка -1").strip()[:300]
            if remark:
                remarks = [str(x).strip() for x in (rp.get("recent_user_remarks") or []) if str(x).strip()]
                if not remarks or remarks[-1] != remark:
                    remarks.append(remark)
                rp["recent_user_remarks"] = remarks[-8:]
                rec["routing_prefs"] = rp
                behavior_store.save(uid, group_id, rec)
        except Exception as e:
            logger.debug("feedback routing_prefs: %s", e)

    if sc == 0:
        return result

    try:
        from core.experience_memory import append_experience_record, append_success, experience_enabled

        if experience_enabled() and user_text:
            if positive:
                append_success(
                    user_text=user_text,
                    intent=intent,
                    module=module,
                    planner_reason=f"user_{source}",
                    assistant_excerpt=assistant_excerpt or "(rated ok)",
                    skill_name=skill,
                )
                result["applied"].append("experience_ok")
            else:
                append_experience_record(
                    user_text=user_text,
                    intent=intent,
                    module=module,
                    planner_reason=f"user_{source}",
                    outcome="fallback",
                    assistant_excerpt=assistant_excerpt,
                    detail=(correction_text or "user negative rating")[:160],
                    skill_name=skill,
                )
                result["applied"].append("experience_bad")
    except Exception as e:
        logger.debug("feedback experience: %s", e)

    try:
        from core.cdc.engine import apply_user_feedback_to_cdc

        if apply_user_feedback_to_cdc(
            user_id=uid,
            user_text=user_text or "[feedback]",
            intent=intent,
            module=module,
            positive=positive,
            skill_name=skill,
        ):
            result["applied"].append("cdc_reputation")
    except Exception as e:
        logger.debug("feedback cdc: %s", e)

    try:
        from core.self_learning.lesson_manager import LessonManager
        from core.self_learning.models import Lesson
        mgr = LessonManager.get_instance()
        if positive:
            for lesson in mgr.load_active_lessons()[:20]:
                lesson.effectiveness_score = min(1.0, float(lesson.effectiveness_score or 0.5) + 0.1)
                mgr.update_lesson(lesson)
            result["applied"].append("lessons_boost")
        elif negative:
            if correction_text.strip():
                lesson = Lesson.new(
                    content=f"Пользователь поправил: {correction_text.strip()[:400]}",
                    source="user_correction",
                    source_context={"intent": intent, "module": module, "skill": skill},
                    category="user_correction",
                    tags=["feedback", "correction"],
                )
                mgr._append_jsonl(lesson)
                result["applied"].append("correction_lesson")
            else:
                for lesson in mgr.load_active_lessons()[:20]:
                    lesson.effectiveness_score = max(0.0, float(lesson.effectiveness_score or 0.5) - 0.2)
                    mgr.update_lesson(lesson)
                result["applied"].append("lessons_penalty")
    except Exception as e:
        logger.debug("feedback lessons: %s", e)

    if negative:
        try:
            from core.user_correction_bus import (
                apply_negative_rating_lesson,
                negative_rating_lesson_instruction,
                set_pending_user_correction,
            )

            inst = negative_rating_lesson_instruction(
                user_text=user_text,
                intent=intent,
                module=module,
                correction_text=correction_text,
            )
            if apply_negative_rating_lesson(
                user_id=uid,
                user_text=user_text,
                intent=intent,
                module=module,
                correction_text=correction_text,
                source=source,
            ):
                result["applied"].append("ephemeral_lesson")
            if behavior_store:
                set_pending_user_correction(
                    behavior_store,
                    uid,
                    group_id,
                    instruction=inst,
                    user_excerpt=user_text[:160],
                    source=source,
                )
                result["applied"].append("pending_correction")
        except Exception as e:
            logger.debug("feedback ephemeral: %s", e)

    if behavior_store and result.get("applied"):
        try:
            rec = behavior_store.load(uid, group_id)
            st = dict(rec.get("session_task") or {})
            st["last_feedback_applied"] = list(result.get("applied") or [])[:8]
            st["last_feedback_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rec["session_task"] = st
            behavior_store.save(uid, group_id, rec)
        except Exception as e:
            logger.debug("feedback session_task: %s", e)

    return result


def parse_rate_args(text: str) -> Tuple[Optional[int], str]:
    """'/rate +1 замечание' → (1, 'замечание')."""
    rest = (text or "").strip()
    if not rest:
        return None, ""
    parts = rest.split(maxsplit=1)
    head = parts[0].strip().lstrip("+")
    tail = parts[1].strip() if len(parts) > 1 else ""
    if head in ("1", "good", "ok", "+", "plus", "да", "👍"):
        return 1, tail
    if head in ("-1", "bad", "-", "minus", "нет", "👎"):
        return -1, tail
    if head in ("0", "neutral"):
        return 0, tail
    try:
        v = int(head)
        if v in (-1, 0, 1):
            return v, tail
    except ValueError:
        pass
    return None, rest
