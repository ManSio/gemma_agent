"""Additive-only prompt modules при profile hop mid-thread."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Set

from core.monitoring import MONITOR
from core.turn_meaning import ACTION_STAY

logger = logging.getLogger(__name__)

_MUST_TO_MODULE: Dict[str, str] = {
    "user_correction": "topic_anchor",
    "topic_anchor": "topic_anchor",
    "active_thread": "active_thread",
}


def additive_prompt_enabled() -> bool:
    """Включён ли additive-only режим prompt modules."""
    raw = os.getenv("TURN_PROMPT_ADDITIVE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _dialogue_state(ctx: Dict[str, Any]) -> Dict[str, Any]:
    ds = ctx.get("dialogue_state")
    return ds if isinstance(ds, dict) else {}


def sticky_modules_from_context(ctx: Dict[str, Any]) -> List[str]:
    """Модули, активные на предыдущем ходе нити."""
    ds = _dialogue_state(ctx)
    sticky = ds.get("sticky_prompt_modules")
    if isinstance(sticky, list):
        return [str(x).strip() for x in sticky if str(x).strip()][:12]
    return []


def profile_hop_detected(ctx: Dict[str, Any], new_profile: str) -> bool:
    """True если brain profile сменился относительно прошлого хода."""
    ds = _dialogue_state(ctx)
    prev = str(ds.get("last_brain_profile") or ctx.get("last_brain_profile") or "").strip().lower()
    cur = str(new_profile or ctx.get("brain_profile") or "").strip().lower()
    return bool(prev and cur and prev != cur)


def is_mid_thread_stay(ctx: Dict[str, Any]) -> bool:
    """True если ход — continuation stay внутри нити."""
    tm = ctx.get("turn_meaning")
    if isinstance(tm, dict):
        if str(tm.get("thread_action") or "").strip().lower() == ACTION_STAY:
            return True
    for key in ("discourse_audit", "discourse_resolution"):
        block = ctx.get(key)
        if isinstance(block, dict) and str(block.get("action") or "").strip().lower() == ACTION_STAY:
            return True
    return False


def _must_block_modules(ctx: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for src_key in ("turn_contract_must_blocks",):
        raw = ctx.get(src_key)
        if isinstance(raw, list):
            for item in raw:
                mod = _MUST_TO_MODULE.get(str(item).strip(), str(item).strip())
                if mod:
                    out.add(mod)
    tc = ctx.get("turn_contract")
    if isinstance(tc, dict):
        for item in tc.get("must_blocks") or []:
            mod = _MUST_TO_MODULE.get(str(item).strip(), str(item).strip())
            if mod:
                out.add(mod)
    return out


def resolve_force_modules(ctx: Dict[str, Any], *, profile: str = "") -> Set[str]:
    """Модули, которые нельзя снять при hop/stay (additive-only)."""
    force = _must_block_modules(ctx)
    if not additive_prompt_enabled():
        return force
    prof = profile or str(ctx.get("brain_profile") or "")
    hop = profile_hop_detected(ctx, prof)
    stay = is_mid_thread_stay(ctx) or bool(ctx.get("correction_turn"))
    if hop or stay:
        force.update(sticky_modules_from_context(ctx))
        if hop:
            MONITOR.inc("turn_prompt_additive_hop_total")
        if stay:
            MONITOR.inc("turn_prompt_additive_stay_total")
    return force


def prepare_additive_context(ctx: Dict[str, Any], *, profile: str = "") -> Dict[str, Any]:
    """Пометить context force-модулями перед сборкой prompt."""
    force = resolve_force_modules(ctx, profile=profile)
    if force:
        ctx["_force_prompt_modules"] = sorted(force)
    else:
        ctx.pop("_force_prompt_modules", None)
    return ctx


def record_active_modules(ctx: Dict[str, Any], active: List[str]) -> None:
    """Union активных модулей в dialogue_state для следующего хода."""
    if not additive_prompt_enabled():
        return
    names = [str(x).strip() for x in (active or []) if str(x).strip()]
    if not names:
        return
    ds = ctx.get("dialogue_state")
    if not isinstance(ds, dict):
        ds = {}
        ctx["dialogue_state"] = ds
    prev = sticky_modules_from_context(ctx)
    merged = list(dict.fromkeys([*prev, *names]))[:12]
    ds["sticky_prompt_modules"] = merged
    MONITOR.inc("turn_prompt_additive_record_total")
