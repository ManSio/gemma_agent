"""Ранняя KV-сессия и guard'ы контекста до сборки промпта."""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from core.brain.session_stickiness import resolve_session as _resolve_sticky_session

logger = logging.getLogger(__name__)


def setup_early_brain_session(
    *,
    user_id: str,
    user_text: str,
    context: Dict[str, Any],
    brain_profile: str,
    dialogue_state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """
    Ранний probe KV-сессии, epoch reset, memory-recall guard, subject-context decay.
    Побочные эффекты: context[\"kv_session_debug\"], context[\"memory_recall_disabled\"], bound_object.
    """
    ctx = context if isinstance(context, dict) else {}
    ds = dialogue_state if isinstance(dialogue_state, dict) else {}

    llm_session_id = ""
    kv_dbg: Dict[str, Any] = {}

    try:
        llm_session_id, kv_dbg = _resolve_sticky_session(
            user_id=user_id,
            group_id=ctx.get("group_id"),
            intent=str(ds.get("last_intent") or "general"),
            prompt_chars=0,
            intent_confidence=None,
            user_text=user_text,
            profile=brain_profile,
        )
        if isinstance(kv_dbg, dict) and kv_dbg:
            ctx["kv_session_debug"] = kv_dbg
    except Exception as e:
        logger.debug("setup_early_brain_session sticky: %s", e, exc_info=True)

    try:
        from core.safety_config import kv_session_reset_enabled

        if kv_session_reset_enabled():
            from core.dialog_state import get_kv_session_epoch

            dialog_epoch = get_kv_session_epoch(
                user_id=str(user_id),
                group_id=ctx.get("group_id"),
            )
            if dialog_epoch > 0:
                llm_session_id = f"{llm_session_id}.ds{dialog_epoch}"
    except Exception as e:
        logger.debug("setup_early_brain_session epoch: %s", e, exc_info=True)

    try:
        from core.memory_recall import memory_recall_allowed

        recent = ctx.get("recent_messages")
        recent_list = recent if isinstance(recent, list) else None
        if not memory_recall_allowed(
            user_text=user_text,
            recent_messages=recent_list,
        ):
            ctx["memory_recall_disabled"] = True
    except Exception as e:
        logger.debug("setup_early_brain_session memory_recall: %s", e, exc_info=True)

    try:
        from core.subject_context import (
            clear_subject_context,
            record_turn as subject_record_turn,
            should_clear as subject_should_clear,
        )

        has_bound = bool(ctx.get("bound_object"))
        subject_record_turn(
            user_id=str(user_id),
            group_id=ctx.get("group_id"),
            has_reference=has_bound,
        )
        if subject_should_clear(
            user_id=str(user_id),
            group_id=ctx.get("group_id"),
        ):
            clear_subject_context(
                user_id=str(user_id),
                group_id=ctx.get("group_id"),
            )
            if "bound_object" in ctx:
                ctx["bound_object"] = None
    except Exception as e:
        logger.debug("setup_early_brain_session subject: %s", e, exc_info=True)

    return llm_session_id, kv_dbg
