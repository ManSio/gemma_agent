"""Единая сверка хода: коллапс TurnStateVector + слоты + footer/audit."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from core.monitoring import MONITOR
from core.turn_state import TurnStateVector, collapse_turn_state

logger = logging.getLogger(__name__)


def _record_slot_reconcile_metrics(tsv: TurnStateVector) -> None:
    """Метрики сверки слотов для self-monitoring."""
    if not tsv.slot_kind_before and not tsv.slot_cleared:
        return
    MONITOR.inc("dialogue_slot_reconcile_total")
    if tsv.slot_cleared:
        kind = tsv.slot_kind_before or "unknown"
        MONITOR.inc("dialogue_slot_cleared_total")
        MONITOR.inc(f"dialogue_slot_cleared_{kind}")


def turn_state_audit_for_emit(
    pre_ctx: Optional[Dict[str, Any]],
    plan: Any = None,
) -> Optional[Dict[str, Any]]:
    """Взять turn_state_audit из plan context или pre_ctx для turn.outcome."""
    if plan is not None:
        try:
            steps = getattr(plan, "steps", None) or []
            if steps:
                args = getattr(steps[0], "args", None)
                if isinstance(args, dict):
                    ctx = args.get("context")
                    if isinstance(ctx, dict):
                        tsa = ctx.get("turn_state_audit")
                        if isinstance(tsa, dict):
                            return dict(tsa)
        except Exception as e:
            logger.debug("turn_state_audit plan: %s", e)
    if isinstance(pre_ctx, dict):
        tsa = pre_ctx.get("turn_state_audit")
        if isinstance(tsa, dict):
            return dict(tsa)
    return None


def reconcile_turn_state(
    user_text: str,
    context: Optional[Dict[str, Any]],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Сверить слоты и коллапсировать TurnStateVector; вернуть (context, mutated)."""
    ctx, tsv, mutated = collapse_turn_state(
        user_text,
        dict(context) if isinstance(context, dict) else {},
        persisted=persisted if isinstance(persisted, dict) else None,
    )
    if mutated or tsv.slot_cleared:
        _record_slot_reconcile_metrics(tsv)
    return ctx, mutated


def hydrate_session_task(context: Dict[str, Any], persisted: Dict[str, Any]) -> None:
    """Прокинуть session_task из behavior store в context для discourse."""
    if "session_task" in context:
        return
    st = persisted.get("session_task")
    if isinstance(st, dict):
        context["session_task"] = dict(st)


def reconcile_turn_with_store(
    user_text: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Сверить слоты, загрузив/сохранив behavior store по user_id из context."""
    ctx = dict(context) if isinstance(context, dict) else {}
    if ctx.get("_turn_state_collapsed") and isinstance(ctx.get("turn_state"), dict):
        return ctx
    uid = str(ctx.get("user_id") or "").strip()
    if not uid:
        reconciled, _ = reconcile_turn_state(user_text, ctx)
        reconciled["_turn_state_collapsed"] = True
        return reconciled
    try:
        store = ctx.get("_behavior_store")
        if store is None:
            from core.behavior_store import BehaviorStore

            store = BehaviorStore()
        gid = ctx.get("group_id")
        rec = store.load(uid, gid)
        hydrate_session_task(ctx, rec)
        reconciled, mutated = reconcile_turn_state(user_text, ctx, persisted=rec)
        if mutated:
            store.save(uid, gid, rec)
        reconciled["_turn_state_collapsed"] = True
        return reconciled
    except Exception as e:
        logger.debug("turn_reconcile store: %s", e)
        reconciled, _ = reconcile_turn_state(user_text, ctx)
        reconciled["_turn_state_collapsed"] = True
        return reconciled


async def apply_discourse_and_collapse_async(
    user_text: str,
    context: Dict[str, Any],
    *,
    llm: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """Единый async-проход: discourse (+ judge) → collapse TSV."""
    ctx = dict(context) if isinstance(context, dict) else {}
    if ctx.get("_turn_state_collapsed") and isinstance(ctx.get("turn_state"), dict):
        return str(ctx.get("user_text") or user_text).strip(), ctx
    try:
        from core.brain.discourse_resolver import apply_discourse_to_context_async

        user_text, ctx = await apply_discourse_to_context_async(user_text, ctx, llm=llm)
    except Exception as e:
        logger.debug("discourse_and_collapse async: %s", e)
    ctx = reconcile_turn_with_store(user_text, ctx)
    return user_text, ctx


def apply_discourse_and_collapse_sync(
    user_text: str,
    context: Dict[str, Any],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any], bool]:
    """Единый sync-проход: discourse → collapse TSV (orchestrator plan)."""
    from core.brain.discourse_resolver import apply_discourse_to_context

    text, ctx = apply_discourse_to_context(user_text, context)
    reconciled, mutated = reconcile_turn_state(text, ctx, persisted=persisted)
    reconciled["_turn_state_collapsed"] = True
    return text, reconciled, mutated
