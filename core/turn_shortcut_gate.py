"""Gating planner shortcuts через TurnMeaning (до weather/geo/pre_llm)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from core.brain.env import env_flag
from core.monitoring import MONITOR
from core.turn_meaning import (
    ACTION_BRANCH,
    ACTION_CORRECT,
    ACTION_STAY,
    REFERENT_AGENT,
    REFERENT_THREAD,
    REFERENT_USER,
    SPEECH_CORRECTION,
    TurnMeaning,
    apply_turn_meaning_to_context,
    resolve_turn_meaning_structural,
    turn_meaning_enabled,
)

logger = logging.getLogger(__name__)

_ALWAYS_ALLOW = frozenset(
    {
        "user_facts_identity_nl",
        "session_meta_recall_nl",
        "dialog_recall_nl",
        "nl_cancel_reminder",
        "nl_weekly_schedule",
        "nl_reminder",
        "article_thread_followup_nl",
    }
)

_WEATHER_GEO_KINDS = frozenset(
    {
        "weather_direct",
        "weather_followup",
        "geo_nearby",
        "telegram_location_direct",
    }
)


def shortcut_gate_enabled() -> bool:
    """Включён ли gate planner shortcuts через TurnMeaning."""
    return env_flag("TURN_SHORTCUT_GATE_ENABLED", default=True) and turn_meaning_enabled()


def prepare_plan_turn_gate(
    user_text: str,
    user_id: Optional[str],
    group_id: Optional[str],
    persisted: Optional[Dict[str, Any]],
) -> Tuple[TurnMeaning, Dict[str, Any]]:
    """Structural TurnMeaning и минимальный context до planner shortcuts."""
    ctx: Dict[str, Any] = {
        "user_id": user_id,
        "group_id": group_id,
        "user_text": user_text,
    }
    rec = persisted if isinstance(persisted, dict) else {}
    if rec:
        rd = rec.get("recent_messages")
        if isinstance(rd, list):
            ctx["recent_dialogue"] = rd
        st = rec.get("session_task")
        if isinstance(st, dict):
            ctx["session_task"] = dict(st)
        ds = rec.get("dialogue_state")
        if isinstance(ds, dict):
            ctx["dialogue_state"] = dict(ds)
        try:
            from core.turn_reconcile import hydrate_session_task

            hydrate_session_task(ctx, rec)
        except Exception as e:
            logger.debug("prepare_plan_turn_gate hydrate: %s", e)
    meaning = resolve_turn_meaning_structural(user_text, ctx)
    return meaning, apply_turn_meaning_to_context(ctx, meaning)


def weather_turn_binds_slot(
    user_text: str,
    persisted: Optional[Dict[str, Any]],
) -> bool:
    """Реплика принимает активный weather-слот по контракту registry."""
    rec = persisted if isinstance(persisted, dict) else None
    if rec is None:
        return False
    try:
        from core.dialogue_slots import get_active_slot
        from core.slot_registry import slot_accepts_turn

        active = get_active_slot(rec)
        if not active:
            return False
        kind = str(active.get("kind") or "").strip()
        if kind != "weather_await_city":
            return False
        rd = rec.get("recent_messages")
        return bool(slot_accepts_turn(kind, user_text, rd, persisted=rec))
    except Exception as e:
        logger.debug("weather_turn_binds_slot: %s", e)
        return False


def planner_shortcut_allowed(
    kind: str,
    meaning: Optional[TurnMeaning],
    *,
    weather_slot_bind: bool = False,
) -> bool:
    """Разрешён ли planner shortcut с учётом TurnMeaning."""
    if not shortcut_gate_enabled() or meaning is None:
        return True
    k = str(kind or "").strip()
    if not k:
        return True
    if k in _ALWAYS_ALLOW:
        return True
    if k == "wall_clock_direct":
        if meaning.thread_action == ACTION_CORRECT or meaning.speech_act == SPEECH_CORRECTION:
            MONITOR.inc("planner_shortcut_blocked_total")
            return False
        return True
    if meaning.thread_action == ACTION_CORRECT or meaning.speech_act == SPEECH_CORRECTION:
        MONITOR.inc("planner_shortcut_blocked_total")
        MONITOR.inc(f"planner_shortcut_blocked_{k}")
        logger.info(
            "[shortcut_gate] blocked kind=%s reason=correction speech_act=%s",
            k,
            meaning.speech_act,
        )
        return False
    if k in _WEATHER_GEO_KINDS:
        if meaning.referent in (REFERENT_AGENT, REFERENT_USER):
            MONITOR.inc("planner_shortcut_blocked_total")
            MONITOR.inc(f"planner_shortcut_blocked_{k}")
            logger.info(
                "[shortcut_gate] blocked kind=%s referent=%s",
                k,
                meaning.referent,
            )
            return False
        if k.startswith("weather"):
            if weather_slot_bind:
                return True
            if meaning.thread_action == ACTION_STAY and meaning.referent == REFERENT_THREAD:
                MONITOR.inc("planner_shortcut_blocked_total")
                MONITOR.inc("planner_shortcut_blocked_weather_stay")
                logger.info("[shortcut_gate] blocked weather on thread stay")
                return False
        return True
    if k == "referential_math_direct":
        return meaning.thread_action != ACTION_CORRECT
    return True


def inject_plan_meaning_into_context(
    target: Dict[str, Any],
    gate_ctx: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Прокинуть ранний turn_meaning в pre_ctx (без повторного structural)."""
    if not isinstance(target, dict) or not isinstance(gate_ctx, dict):
        return target
    for key in ("turn_meaning", "turn_meaning_audit", "session_task"):
        if key in gate_ctx and key not in target:
            target[key] = gate_ctx[key]
    return target
