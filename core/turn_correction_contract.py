"""Correction turn: must_blocks и full-prompt при исправлении пользователя."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

from core.monitoring import MONITOR
from core.turn_meaning import ACTION_CORRECT, SPEECH_CORRECTION

logger = logging.getLogger(__name__)

_CORRECTION_BLOCKS = ("user_correction", "topic_anchor", "active_thread")


def correction_override_enabled() -> bool:
    """Включён ли correction override (full prompt blocks)."""
    raw = os.getenv("TURN_CORRECTION_OVERRIDE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_correction_turn(context: Optional[Dict[str, Any]]) -> bool:
    """Ход — пользовательское исправление предыдущего ответа."""
    ctx = context if isinstance(context, dict) else {}
    tm = ctx.get("turn_meaning")
    if isinstance(tm, dict):
        if str(tm.get("thread_action") or "").strip().lower() == ACTION_CORRECT:
            return True
        if str(tm.get("speech_act") or "").strip().lower() == SPEECH_CORRECTION:
            return True
    for key in ("discourse_audit", "discourse_resolution"):
        block = ctx.get(key)
        if isinstance(block, dict) and str(block.get("action") or "").strip().lower() == ACTION_CORRECT:
            return True
    if ctx.get("expects_correction") or ctx.get("correction_pending"):
        return True
    return False


def must_blocks_for_context(context: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """Обязательные prompt blocks для TurnContract."""
    ctx = context if isinstance(context, dict) else {}
    blocks: List[str] = []
    if correction_override_enabled() and is_correction_turn(ctx):
        blocks.extend(_CORRECTION_BLOCKS)
        MONITOR.inc("turn_correction_must_blocks_total")
    else:
        anchor = ""
        tc = ctx.get("turn_contract")
        if isinstance(tc, dict):
            anchor = str(tc.get("topic_anchor") or "").strip()
        if anchor:
            blocks.append("topic_anchor")
    if str(ctx.get("active_thread_block") or "").strip() and "active_thread" not in blocks:
        blocks.append("active_thread")
    return tuple(dict.fromkeys(blocks))


def apply_correction_override(context: Dict[str, Any]) -> Dict[str, Any]:
    """Пометить context: full prompt, без slim, must_blocks."""
    if not correction_override_enabled() or not is_correction_turn(context):
        return context
    ctx = context
    ctx["brain_force_full_prompt"] = True
    ctx["correction_turn"] = True
    blocks = must_blocks_for_context(ctx)
    if blocks:
        ctx["turn_contract_must_blocks"] = list(blocks)
        tc = ctx.get("turn_contract")
        if isinstance(tc, dict):
            tc = dict(tc)
            tc["must_blocks"] = list(blocks)
            ctx["turn_contract"] = tc
    MONITOR.inc("turn_correction_override_total")
    return ctx
