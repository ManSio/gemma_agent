"""Post-reconcile spine: ephemeral hints и profile lock после meaning/discourse/collapse."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

_SPINE_VERSION = 1


def refresh_post_reconcile_payload(
    context: Dict[str, Any],
    user_text: str,
    *,
    user_id: str = "",
) -> Dict[str, Any]:
    """Единая точка после reconcile: ephemeral + meaning profile lock в context."""
    ctx = context
    ut = (user_text or str(ctx.get("user_text") or "")).strip()
    try:
        from core.turn_meaning import profile_override_from_meaning

        prof = profile_override_from_meaning(ctx)
        if prof:
            ctx["meaning_profile_lock"] = prof
            ctx["brain_profile_meaning_override"] = prof
        else:
            ctx.pop("meaning_profile_lock", None)
            ctx.pop("brain_profile_meaning_override", None)
    except Exception as e:
        logger.debug("spine profile lock: %s", e)

    try:
        from core.feedback_contract import brain_addon_for_context

        ctx["ephemeral_lessons_brain_addon"] = brain_addon_for_context(ut, ctx).strip()
    except Exception as e:
        logger.debug("spine ephemeral: %s", e)
        ctx["ephemeral_lessons_brain_addon"] = ""

    ctx["post_reconcile_spine_ready"] = True
    ctx["_post_reconcile_spine_v"] = _SPINE_VERSION
    if user_id:
        ctx.setdefault("user_id", user_id)
    try:
        from core.turn_lane_spine import apply_sticky_lane_and_profile

        persisted = None
        bs = ctx.get("_behavior_store")
        uid = str(ctx.get("user_id") or user_id or "").strip()
        gid = ctx.get("group_id")
        if bs and uid and hasattr(bs, "load"):
            try:
                persisted = bs.load(uid, gid)
            except Exception as e:
                logger.debug("sticky lane load: %s", e)
        apply_sticky_lane_and_profile(ctx, persisted=persisted if isinstance(persisted, dict) else None)
    except Exception as e:
        logger.debug("turn_lane_spine: %s", e)
    try:
        from core.turn_correction_contract import apply_correction_override

        apply_correction_override(ctx)
    except Exception as e:
        logger.debug("correction_override: %s", e)
    MONITOR.inc("turn_decision_spine_refresh_total")
    return ctx


def ephemeral_lessons_hint_for_context(
    context: Dict[str, Any],
    user_text: str,
) -> str:
    """Ephemeral block для prompt: всегда из feedback_contract, не из stale cache."""
    if not isinstance(context, dict):
        return ""
    ut = (user_text or str(context.get("user_text") or "")).strip()
    if context.get("post_reconcile_spine_ready"):
        return str(context.get("ephemeral_lessons_brain_addon") or "").strip()
    try:
        from core.feedback_contract import brain_addon_for_context

        return brain_addon_for_context(ut, context).strip()
    except Exception as e:
        logger.debug("ephemeral_lessons_hint: %s", e)
        return ""


def apply_meaning_profile_lock(
    brain_profile: str,
    context: Optional[Dict[str, Any]],
) -> str:
    """Финальный профиль: meaning lock после classifier/continuation/batch."""
    prof = str(brain_profile or "").strip()
    ctx = context if isinstance(context, dict) else {}
    lock = str(ctx.get("meaning_profile_lock") or "").strip()
    if not lock:
        return prof
    if ctx.get("brain_force_batch_profile"):
        return prof
    try:
        from core.brain.profile_registry import is_valid_profile

        if is_valid_profile(lock):
            if prof != lock:
                MONITOR.inc("brain_meaning_profile_lock_applied_total")
            return lock
    except Exception as e:
        logger.debug("apply_meaning_profile_lock: %s", e)
    return prof


def intent_hint_from_turn_meaning(context: Optional[Dict[str, Any]]) -> str:
    """Intent для planner из TurnMeaning (до keyword heuristics)."""
    ctx = context if isinstance(context, dict) else {}
    tm = ctx.get("turn_meaning")
    if not isinstance(tm, dict):
        return ""
    referent = str(tm.get("referent") or "").strip().lower()
    action = str(tm.get("thread_action") or "").strip().lower()
    if action == "correct":
        dr = ctx.get("discourse_resolution")
        if isinstance(dr, dict):
            inh = str(dr.get("inherit_intent") or "").strip().lower()
            if inh and inh not in {"", "empty", "unknown"}:
                return inh
        ds = ctx.get("dialogue_state")
        if isinstance(ds, dict):
            li = str(ds.get("last_intent") or "").strip().lower()
            if li and li not in {"", "empty", "unknown"}:
                return li
        return "general"
    if referent == "agent":
        return "explain"
    if action in {"stay", "branch"} and referent in {"thread", "world", "user"}:
        try:
            from core.brain.discourse_resolver import inherited_intent_from_context

            inh = inherited_intent_from_context(ctx)
            if inh and inh not in {"", "empty", "unknown"}:
                return inh
        except Exception as e:
            logger.debug("intent_hint inherit: %s", e)
    return ""
