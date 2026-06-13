"""
P3: дешёвые direct_reply в orchestrator.plan до chat/brain (без OpenRouter).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Варианты direct_reply из plan() → __fallback__; должны быть в orchestrator._execute_step whitelist.
PRE_LLM_DIRECT_VARIANTS: frozenset[str] = frozenset(
    {
        "wall_clock_direct",
        "dialog_recall_nl",
        "session_meta_recall_nl",
        "user_facts_identity_nl",
        "article_thread_followup_nl",
    }
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def pre_llm_plan_enabled() -> bool:
    return _truthy("PRE_LLM_PLAN_ENABLED", True)


def try_pre_llm_direct_plan(
    *,
    user_id: str,
    group_id: Optional[str],
    text: str,
    persisted: Optional[Dict[str, Any]],
    input_meta: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str]]:
    """
    Возвращает (planner_reason, direct_reply) или None — идти в brain/LLM.
    """
    if not pre_llm_plan_enabled():
        return None
    t = (text or "").strip()
    uid = str(user_id or "").strip()
    if not uid or not t or t.lstrip().startswith("/"):
        return None
    rec = persisted if isinstance(persisted, dict) else {}
    meta = input_meta if isinstance(input_meta, dict) else {}
    recent = rec.get("recent_messages")
    if not isinstance(recent, list):
        recent = None

    try:
        from core.intent_heuristics import detect_pre_llm_shortcut

        lane = detect_pre_llm_shortcut(t, recent_dialogue=recent, persisted=rec)
    except Exception as e:
        logger.debug("pre_llm_plan detect: %s", e)
        lane = ""

    if lane == "wall_clock":
        try:
            from core.timezone_inference import try_wall_clock_direct_reply

            reply = try_wall_clock_direct_reply(
                t,
                user_facts=rec.get("user_facts"),
                recent_dialogue=recent,
                telegram_message_unix=meta.get("telegram_message_date_unix"),
            )
            if reply.strip():
                return ("wall_clock_direct", reply.strip())
        except Exception as e:
            logger.debug("pre_llm_plan wall_clock: %s", e)

    if lane == "session_meta_recall":
        try:
            from core.memory_recall_facade import (
                build_session_meta_recall_reply,
                session_meta_recall_enabled,
            )

            if session_meta_recall_enabled():
                ctx: Dict[str, Any] = {
                    "session_first_user_text": str(rec.get("session_first_user_text") or "").strip(),
                    "dialogue_summary": str(rec.get("dialogue_summary") or "").strip(),
                }
                reply = build_session_meta_recall_reply(
                    user_id=uid,
                    group_id=group_id,
                    context=ctx,
                )
                if reply.strip():
                    return ("session_meta_recall_nl", reply.strip())
        except Exception as e:
            logger.debug("pre_llm_plan session_meta_recall: %s", e)

    if lane == "user_facts_identity":
        try:
            from core.behavior_store import BehaviorStore
            from core.user_facts import build_user_facts_identity_reply, brain_user_facts_from_store

            facts, _meta = brain_user_facts_from_store(BehaviorStore(), uid, group_id)
            reply = build_user_facts_identity_reply(facts)
            if reply.strip():
                return ("user_facts_identity_nl", reply.strip())
        except Exception as e:
            logger.debug("pre_llm_plan user_facts_identity: %s", e)

    if lane == "dialog_recall":
        try:
            from core.memory_recall_facade import (
                build_slash_recall_bundle,
                nl_dialog_recall_route_enabled,
            )

            if nl_dialog_recall_route_enabled():
                ctx: Dict[str, Any] = {
                    "user_id": uid,
                    "group_id": group_id,
                    "user_facts": rec.get("user_facts") or {},
                    "recent_dialogue": recent,
                    "recent_messages": recent,
                    "telegram_message_date_unix": meta.get("telegram_message_date_unix"),
                    "dialogue_summary": str(rec.get("dialogue_summary") or "").strip(),
                }
                reply = build_slash_recall_bundle(
                    user_id=uid,
                    group_id=group_id,
                    context=ctx,
                    mode="summary",
                )
                if reply.strip():
                    return ("dialog_recall_nl", reply.strip())
        except Exception as e:
            logger.debug("pre_llm_plan dialog_recall: %s", e)

    if lane == "article_thread":
        try:
            from core.article_thread_followup import (
                should_handle_article_thread_followup,
                try_article_thread_followup_reply_sync,
            )

            if should_handle_article_thread_followup(t, recent, rec):
                from core.article_thread_followup import finalize_article_thread_pre_llm_reply

                reply = try_article_thread_followup_reply_sync(
                    t,
                    recent_dialogue=recent,
                    persisted=rec,
                    user_id=uid,
                )
                body = finalize_article_thread_pre_llm_reply(
                    t,
                    reply,
                    recent_dialogue=recent,
                    persisted=rec,
                )
                if body:
                    return ("article_thread_followup_nl", body)
        except Exception as e:
            logger.debug("pre_llm_plan article_thread: %s", e)

    return None
