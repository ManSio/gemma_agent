"""Финализация direct Plan: plan_turn_meaning, turn_contract, meta embed."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _plan_input_dict(plan: Any) -> Optional[Dict[str, Any]]:
    if not plan or not getattr(plan, "steps", None):
        return None
    try:
        args0 = plan.steps[0].args or {}
        inp = args0.get("input")
        return inp if isinstance(inp, dict) else None
    except Exception:
        return None


def ensure_plan_turn_meaning_on_meta(
    input_meta: Dict[str, Any],
    *,
    user_text: str,
    persisted: Optional[Dict[str, Any]] = None,
    plan_meaning: Any = None,
) -> Dict[str, Any]:
    """Гарантировать plan_turn_meaning в input meta (Phase 0.3)."""
    if plan_meaning is not None:
        if hasattr(plan_meaning, "to_dict"):
            input_meta["plan_turn_meaning"] = plan_meaning.to_dict()
        elif isinstance(plan_meaning, dict):
            input_meta["plan_turn_meaning"] = dict(plan_meaning)
        return input_meta
    if isinstance(input_meta.get("plan_turn_meaning"), dict) and input_meta["plan_turn_meaning"]:
        return input_meta
    try:
        from core.turn_shortcut_gate import prepare_plan_turn_gate

        uid = str(input_meta.get("user_id") or "")
        gid = input_meta.get("group_id")
        if uid and isinstance(persisted, dict):
            meaning, _ctx = prepare_plan_turn_gate(user_text, uid, gid, persisted)
            input_meta["plan_turn_meaning"] = meaning.to_dict()
            return input_meta
    except Exception as e:
        logger.debug("ensure_plan_turn_meaning gate: %s", e)
    try:
        from core.turn_meaning import resolve_turn_meaning_structural

        recent = []
        if isinstance(persisted, dict):
            recent = persisted.get("recent_messages") or []
        meaning = resolve_turn_meaning_structural(
            user_text,
            {"recent_dialogue": recent if isinstance(recent, list) else []},
        )
        input_meta["plan_turn_meaning"] = meaning.to_dict()
    except Exception as e:
        logger.debug("ensure_plan_turn_meaning structural: %s", e)
    return input_meta


def ensure_turn_contract_on_meta(
    input_meta: Dict[str, Any],
    *,
    user_text: str,
    persisted: Optional[Dict[str, Any]] = None,
    plan_meaning: Any = None,
    profile: str = "",
) -> Dict[str, Any]:
    """Обновить turn_contract audit на meta."""
    try:
        from core.turn_contract import build_turn_contract, turn_contract_enabled

        if not turn_contract_enabled():
            return input_meta
        tm = plan_meaning
        if tm is None:
            tm = input_meta.get("plan_turn_meaning")
        tc = build_turn_contract(
            trace_id=str(input_meta.get("trace_id") or ""),
            generation=int(input_meta.get("turn_generation") or 0),
            turn_meaning=tm,
            persisted=persisted if isinstance(persisted, dict) else None,
            user_text=user_text,
            profile=profile or str(input_meta.get("profile") or ""),
            short_circuit=str(
                (input_meta.get("turn_contract") or {}).get("short_circuit")
                or input_meta.get("planner_bypass")
                or ""
            ),
            input_meta=input_meta,
        )
        input_meta["turn_contract"] = tc.to_dict()
    except Exception as e:
        logger.debug("ensure_turn_contract_on_meta: %s", e)
    return input_meta


def embed_meta_in_plan_input(plan: Any, input_meta: Dict[str, Any]) -> None:
    """Записать meta обратно в plan.steps[0].args.input."""
    inp = _plan_input_dict(plan)
    if inp is None:
        return
    inp["meta"] = dict(input_meta)
    try:
        uid = str(input_meta.get("user_id") or "")
        if uid:
            args0 = plan.steps[0].args or {}
            ctx = args0.get("context")
            if isinstance(ctx, dict) and not ctx.get("user_id"):
                ctx["user_id"] = uid
    except Exception as e:
        logger.debug("embed_meta_in_plan_input: %s", e)


def stamp_plan_turn_hash_on_meta(input_meta: Dict[str, Any], plan: Any) -> None:
    """plan_turn_hash для drift-check."""
    try:
        from core.turn_hash import plan_turn_hash_from_meta

        input_meta["plan_turn_hash"] = plan_turn_hash_from_meta(input_meta, plan)
    except Exception as e:
        logger.debug("stamp_plan_turn_hash_on_meta: %s", e)


def finalize_direct_plan(
    plan: Any,
    input_meta: Dict[str, Any],
    *,
    user_text: str = "",
    persisted: Optional[Dict[str, Any]] = None,
    plan_meaning: Any = None,
    profile: str = "",
) -> Any:
    """Полная финализация direct Plan перед return/execute."""
    if not isinstance(input_meta, dict):
        return plan
    uid = str(input_meta.get("user_id") or "")
    if uid and not input_meta.get("user_id"):
        input_meta["user_id"] = uid
    ensure_plan_turn_meaning_on_meta(
        input_meta,
        user_text=user_text,
        persisted=persisted,
        plan_meaning=plan_meaning,
    )
    try:
        from core.turn_delivery_store import patch_plan_meta_shortcut_from_step

        embed_meta_in_plan_input(plan, input_meta)
        patch_plan_meta_shortcut_from_step(plan)
        inp = _plan_input_dict(plan)
        if inp is not None and isinstance(inp.get("meta"), dict):
            input_meta.update(inp["meta"])
    except Exception as e:
        logger.debug("finalize_direct_plan shortcut: %s", e)
    ensure_turn_contract_on_meta(
        input_meta,
        user_text=user_text,
        persisted=persisted,
        plan_meaning=plan_meaning,
        profile=profile,
    )
    stamp_plan_turn_hash_on_meta(input_meta, plan)
    embed_meta_in_plan_input(plan, input_meta)
    return plan
