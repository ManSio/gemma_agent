"""Единая сверка хода: TurnMeaning → discourse → коллапс TurnStateVector."""
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
                            out = dict(tsa)
                            tma = ctx.get("turn_meaning_audit")
                            if isinstance(tma, dict):
                                out.update(tma)
                            return out
        except Exception as e:
            logger.debug("turn_state_audit plan: %s", e)
    if isinstance(pre_ctx, dict):
        tsa = pre_ctx.get("turn_state_audit")
        if isinstance(tsa, dict):
            out = dict(tsa)
            tma = pre_ctx.get("turn_meaning_audit")
            if isinstance(tma, dict):
                out.update(tma)
            return out
    return None


def turn_meaning_audit_for_emit(pre_ctx: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Компактный audit TurnMeaning для turn.outcome."""
    if isinstance(pre_ctx, dict):
        tma = pre_ctx.get("turn_meaning_audit")
        if isinstance(tma, dict):
            return dict(tma)
    return None


def _needs_async_meaning_upgrade(ctx: Dict[str, Any]) -> bool:
    """Нужен ли повторный проход с LLM после sync plan (judge bypass fix)."""
    if not ctx.get("_turn_state_collapsed"):
        return False
    tm = ctx.get("turn_meaning")
    if not isinstance(tm, dict):
        return True
    if str(tm.get("source") or "") == "llm":
        return False
    try:
        from core.turn_meaning import TurnMeaning, turn_meaning_llm_needed

        meaning = TurnMeaning(
            speech_act=str(tm.get("speech_act") or ""),
            referent=str(tm.get("referent") or ""),
            thread_action=str(tm.get("thread_action") or ""),
            inherit_thread=bool(tm.get("inherit_thread")),
            source=str(tm.get("source") or "structural"),
            confidence=float(tm.get("confidence") or 0.0),
            reason=str(tm.get("reason") or ""),
        )
        return turn_meaning_llm_needed(meaning, ctx)
    except Exception as e:
        logger.debug("needs_async_meaning_upgrade: %s", e)
    try:
        from core.brain.discourse_resolver import _needs_judge_upgrade

        return _needs_judge_upgrade(ctx)
    except Exception as e:
        logger.debug("needs_async_meaning_upgrade judge: %s", e)
        return False


def _clear_collapse_ephemeral(ctx: Dict[str, Any]) -> None:
    """Сбросить флаги collapse для повторной сверки после LLM judge."""
    for key in (
        "_turn_state_collapsed",
        "turn_state",
        "turn_state_audit",
        "_discourse_applied",
        "discourse_resolution",
        "discourse_audit",
        "active_thread_block",
    ):
        ctx.pop(key, None)


def _merge_routing_hint(ctx: Dict[str, Any], hint: str) -> None:
    """Добавить hint в routing_prefs_hint без дублирования."""
    h = (hint or "").strip()
    if not h:
        return
    prev = str(ctx.get("routing_prefs_hint") or "").strip()
    if h in prev:
        return
    ctx["routing_prefs_hint"] = f"{prev}\n\n{h}".strip() if prev else h


async def _apply_meaning_discourse_collapse_async(
    user_text: str,
    context: Dict[str, Any],
    *,
    llm: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
    force_recollapse: bool = False,
) -> Tuple[str, Dict[str, Any], bool]:
    """Meaning → discourse (async+judge) → collapse; единый async-проход."""
    from core.brain.discourse_resolver import apply_discourse_to_context_async
    from core.turn_meaning import (
        apply_turn_meaning_to_context,
        resolve_turn_meaning_async,
        routing_hint_for_meaning,
    )

    ctx = dict(context)
    if force_recollapse:
        _clear_collapse_ephemeral(ctx)

    meaning = await resolve_turn_meaning_async(user_text, ctx, llm=llm)
    ctx = apply_turn_meaning_to_context(ctx, meaning)
    _merge_routing_hint(ctx, routing_hint_for_meaning(meaning.to_dict()))

    user_text, ctx = await apply_discourse_to_context_async(user_text, ctx, llm=llm)

    mutated = False
    if persisted is not None:
        reconciled, mutated = reconcile_turn_state(user_text, ctx, persisted=persisted)
    else:
        reconciled = reconcile_turn_with_store(user_text, ctx, force=force_recollapse)
        mutated = False
    reconciled["_turn_state_collapsed"] = True
    reconciled["_turn_meaning_resolved"] = True
    return user_text, reconciled, mutated


def reconcile_turn_state(
    user_text: str,
    context: Optional[Dict[str, Any]],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Сверить слоты и коллапсировать TurnStateVector; вернуть (context, mutated)."""
    ctx_in = dict(context) if isinstance(context, dict) else {}
    ctx, tsv, mutated = collapse_turn_state(
        user_text,
        ctx_in,
        persisted=persisted if isinstance(persisted, dict) else None,
    )
    tma = ctx_in.get("turn_meaning_audit")
    if isinstance(tma, dict) and isinstance(ctx.get("turn_state_audit"), dict):
        merged = dict(ctx["turn_state_audit"])
        merged.update(tma)
        ctx["turn_state_audit"] = merged
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
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Сверить слоты, загрузив/сохранив behavior store по user_id из context."""
    ctx = dict(context) if isinstance(context, dict) else {}
    if not force and ctx.get("_turn_state_collapsed") and isinstance(ctx.get("turn_state"), dict):
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
    """Единый async-проход: TurnMeaning + discourse (+ judge) → collapse TSV."""
    ctx = dict(context) if isinstance(context, dict) else {}
    if ctx.get("_turn_state_collapsed") and isinstance(ctx.get("turn_state"), dict):
        if _needs_async_meaning_upgrade(ctx):
            uid = str(ctx.get("user_id") or "").strip()
            persisted: Optional[Dict[str, Any]] = None
            store = None
            gid = ctx.get("group_id")
            if uid:
                try:
                    store = ctx.get("_behavior_store")
                    if store is None:
                        from core.behavior_store import BehaviorStore

                        store = BehaviorStore()
                    persisted = store.load(uid, gid)
                    hydrate_session_task(ctx, persisted)
                except Exception as e:
                    logger.debug("turn_reconcile upgrade load: %s", e)
            user_text, ctx, mutated = await _apply_meaning_discourse_collapse_async(
                user_text,
                ctx,
                llm=llm,
                persisted=persisted,
                force_recollapse=True,
            )
            if mutated and store is not None and uid and isinstance(persisted, dict):
                try:
                    store.save(uid, gid, persisted)
                except Exception as e:
                    logger.debug("turn_reconcile upgrade save: %s", e)
            return user_text, ctx
        return str(ctx.get("user_text") or user_text).strip(), ctx

    user_text, ctx, _ = await _apply_meaning_discourse_collapse_async(
        user_text, ctx, llm=llm, force_recollapse=False
    )
    return user_text, ctx


def apply_discourse_and_collapse_sync(
    user_text: str,
    context: Dict[str, Any],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any], bool]:
    """Sync-проход plan(): structural TurnMeaning → discourse → collapse (LLM в brain)."""
    from core.brain.discourse_resolver import apply_discourse_to_context
    from core.turn_meaning import (
        apply_turn_meaning_to_context,
        resolve_turn_meaning_structural,
        routing_hint_for_meaning,
    )

    ctx = dict(context) if isinstance(context, dict) else {}
    if persisted is not None:
        hydrate_session_task(ctx, persisted)

    meaning = resolve_turn_meaning_structural(user_text, ctx)
    ctx = apply_turn_meaning_to_context(ctx, meaning)
    _merge_routing_hint(ctx, routing_hint_for_meaning(meaning.to_dict()))

    text, ctx = apply_discourse_to_context(user_text, ctx)
    reconciled, mutated = reconcile_turn_state(text, ctx, persisted=persisted)
    reconciled["_turn_state_collapsed"] = True
    return text, reconciled, mutated
