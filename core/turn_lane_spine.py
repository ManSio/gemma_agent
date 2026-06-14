"""Sticky lane/profile при discourse stay — стабильность нити без blind lock."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from core.monitoring import MONITOR
from core.turn_meaning import ACTION_CORRECT, ACTION_STAY

logger = logging.getLogger(__name__)


def sticky_lane_enabled() -> bool:
    """Включён ли sticky lane при discourse stay."""
    raw = os.getenv("TURN_STICKY_LANE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _discourse_action(ctx: Dict[str, Any]) -> str:
    for key in ("discourse_audit", "discourse_resolution"):
        block = ctx.get(key)
        if isinstance(block, dict):
            act = str(block.get("action") or "").strip().lower()
            if act:
                return act
    return ""


def _inherit_profile(ctx: Dict[str, Any], persisted: Optional[Dict[str, Any]] = None) -> str:
    for key in ("discourse_audit", "discourse_resolution"):
        block = ctx.get(key)
        if isinstance(block, dict):
            prof = str(block.get("inherit_profile") or "").strip()
            if prof:
                return prof
    ds = ctx.get("dialogue_state")
    if isinstance(ds, dict):
        prof = str(ds.get("_discourse_inherit_profile") or "").strip()
        if prof:
            return prof
    rec = persisted if isinstance(persisted, dict) else {}
    st = rec.get("session_task")
    if isinstance(st, dict):
        for k in ("kv_profile", "last_profile"):
            v = str(st.get(k) or "").strip()
            if v:
                return v
    rp = rec.get("routing_prefs")
    if isinstance(rp, dict):
        lp = str(rp.get("last_brain_profile") or "").strip()
        if lp:
            return lp
    return ""


def is_sticky_stay_turn(ctx: Dict[str, Any]) -> bool:
    """True если ход — continuation stay (не pivot/correct)."""
    tm = ctx.get("turn_meaning")
    if not isinstance(tm, dict):
        tm = {}
    action = _discourse_action(ctx) or str(tm.get("thread_action") or "").strip().lower()
    if action == ACTION_CORRECT:
        return False
    if action == ACTION_STAY:
        return True
    return str(tm.get("thread_action") or "").strip().lower() == ACTION_STAY and bool(
        tm.get("inherit_thread")
    )


def apply_sticky_lane_and_profile(
    context: Dict[str, Any],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """При discourse stay — удержать lane/profile; pivot/correct не блокируем."""
    if not sticky_lane_enabled():
        return context
    ctx = context if isinstance(context, dict) else {}
    if not is_sticky_stay_turn(ctx):
        return ctx

    prof = _inherit_profile(ctx, persisted)
    if prof:
        try:
            from core.brain.profile_registry import is_valid_profile

            if is_valid_profile(prof):
                if str(ctx.get("meaning_profile_lock") or "") != prof:
                    MONITOR.inc("turn_sticky_profile_lock_total")
                ctx["meaning_profile_lock"] = prof
                ctx["brain_profile_meaning_override"] = prof
                ctx["sticky_profile"] = prof
        except Exception as e:
            logger.debug("sticky profile: %s", e)

    try:
        from core.turn_contract import lane_from_profile

        lane = lane_from_profile(prof or str(ctx.get("brain_profile") or ""))
        ctx["sticky_lane"] = lane
        tc = ctx.get("turn_contract")
        if isinstance(tc, dict):
            tc = dict(tc)
            tc["lane"] = lane
            if prof:
                tc["sticky_profile"] = prof[:48]
            ctx["turn_contract"] = tc
        MONITOR.inc("turn_sticky_lane_applied_total")
    except Exception as e:
        logger.debug("sticky lane: %s", e)
    return ctx
