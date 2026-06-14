"""Отложенная запись STM: store только после успешной доставки текста."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


def defer_turn_store_enabled() -> bool:
    """Store после send (не в execute_plan), если включён TurnContract."""
    raw = os.getenv("TURN_DEFER_STORE_ENABLED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from core.turn_contract import turn_contract_enabled

        return turn_contract_enabled()
    except Exception:
        return True


def generation_from_plan_meta(plan: Any) -> int:
    """Извлечь turn_generation из plan input meta."""
    if not plan or not getattr(plan, "steps", None):
        return 0
    try:
        inp = (plan.steps[0].args or {}).get("input") or {}
        meta = inp.get("meta") if isinstance(inp, dict) else {}
        if not isinstance(meta, dict):
            return 0
        tc = meta.get("turn_contract") if isinstance(meta.get("turn_contract"), dict) else {}
        return int(meta.get("turn_generation") or tc.get("generation") or 0)
    except (TypeError, ValueError, IndexError):
        return 0


def generation_stale_for_chat(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    generation: int,
) -> bool:
    """True если generation устарел относительно текущего чата."""
    if generation <= 0 or not behavior_store or not user_id:
        return False
    try:
        return int(generation) != int(behavior_store.get_turn_generation(user_id, group_id))
    except (TypeError, ValueError):
        return False


def patch_turn_contract_shortcut(
    input_meta: Optional[Dict[str, Any]],
    short_circuit: str,
    *,
    profile: str = "",
) -> None:
    """Обновить turn_contract при planner direct shortcut (weather/news/…)."""
    if not isinstance(input_meta, dict):
        return
    sc = (short_circuit or "").strip()
    if not sc:
        return
    try:
        from core.turn_contract import lane_from_profile

        tc = dict(input_meta.get("turn_contract") or {})
        tc["short_circuit"] = sc[:48]
        tc["lane"] = lane_from_profile(profile, short_circuit=sc)
        input_meta["turn_contract"] = tc
    except Exception as e:
        logger.debug("patch_turn_contract_shortcut: %s", e)


def patch_plan_meta_shortcut_from_step(plan: Any) -> None:
    """Патч turn_contract из fallback_variant шага plan (все direct returns)."""
    if not plan or not getattr(plan, "steps", None):
        return
    try:
        args0 = plan.steps[0].args or {}
        variant = str(args0.get("fallback_variant") or "").strip()
        if not variant:
            return
        inp = args0.get("input") or {}
        meta = inp.get("meta") if isinstance(inp, dict) else None
        patch_turn_contract_shortcut(meta if isinstance(meta, dict) else None, variant)
        try:
            from core.short_circuit_registry import record_short_circuit_use

            tid = ""
            if isinstance(meta, dict):
                tid = str(meta.get("trace_id") or "")
            record_short_circuit_use(variant, input_meta=meta if isinstance(meta, dict) else None, trace_id=tid)
        except Exception as e:
            logger.debug("record_short_circuit: %s", e)
    except Exception as e:
        logger.debug("patch_plan_meta_shortcut: %s", e)


def build_pending_turn_store(
    *,
    user_payload: str,
    draft_assistant: str,
    dialogue_patch: Optional[Dict[str, Any]] = None,
    group_patch: Optional[Dict[str, Any]] = None,
    blended_style: Any = None,
    micro_emotion: Any = None,
    telegram_is_admin: bool = False,
    turn_meta: Optional[Dict[str, Any]] = None,
    pre_ctx: Optional[Dict[str, Any]] = None,
    cdc_policy_patch: Optional[Dict[str, Any]] = None,
    outcome_all: str = "",
    skill_name: str = "",
    patch_session_task: Optional[Dict[str, Any]] = None,
    plan_fallback_variant: str = "",
    news_digest_context: Any = None,
    trace_meta: Optional[Dict[str, Any]] = None,
    ds_exec: Optional[Dict[str, Any]] = None,
    recent_before: Any = None,
    plan_steps: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Сериализуемый pending для finalize после send."""
    ctx = pre_ctx if isinstance(pre_ctx, dict) else {}
    return {
        "user_payload": str(user_payload or ""),
        "draft_assistant": str(draft_assistant or "")[:4000],
        "dialogue_patch": dict(dialogue_patch or {}),
        "group_patch": dict(group_patch or {}),
        "blended_style": blended_style,
        "micro_emotion": micro_emotion,
        "telegram_is_admin": bool(telegram_is_admin),
        "turn_meta": dict(turn_meta or {}),
        "pre_ctx_cdc_policy": dict(ctx.get("cdc_policy") or {}) if ctx.get("cdc_policy") else None,
        "pre_ctx_affect_state": dict(ctx.get("affect_state") or {}) if ctx.get("affect_state") else None,
        "pre_ctx_self_model": dict(ctx.get("self_model") or {}) if ctx.get("self_model") else None,
        "cdc_policy_patch": dict(cdc_policy_patch or {}) if cdc_policy_patch else None,
        "outcome_all": str(outcome_all or ""),
        "skill_name": str(skill_name or ""),
        "patch_session_task": dict(patch_session_task or {}) if patch_session_task else None,
        "plan_fallback_variant": str(plan_fallback_variant or ""),
        "news_digest_context": news_digest_context if isinstance(news_digest_context, dict) else None,
        "trace_meta": dict(trace_meta or {}) if trace_meta else None,
        "ds_exec": dict(ds_exec or {}) if ds_exec else None,
        "recent_before": recent_before if isinstance(recent_before, list) else [],
        "plan_steps": list(plan_steps or []),
    }


def attach_pending_to_outputs(
    outputs: List[Any],
    pending: Dict[str, Any],
) -> None:
    """Прикрепить pending_turn_store к первому text output."""
    if not pending or not outputs:
        return
    for out in outputs:
        if getattr(out, "type", None) == "text":
            meta = dict(getattr(out, "meta", None) or {})
            meta["pending_turn_store"] = pending
            try:
                from core.models import Output

                idx = outputs.index(out)
                outputs[idx] = Output(type=out.type, payload=out.payload, meta=meta)
            except Exception:
                meta_attr = getattr(out, "meta", None)
                if isinstance(meta_attr, dict):
                    meta_attr["pending_turn_store"] = pending
            return


def persist_turn_after_delivery(
    *,
    behavior_store: Any,
    goal_engine: Any,
    user_id: str,
    group_id: Optional[str],
    assistant_text: str,
    pending: Dict[str, Any],
    generation: int = 0,
    orchestrator: Any = None,
) -> bool:
    """Записать ход в behavior_store после успешной доставки в Telegram/API."""
    if not behavior_store or not user_id or not isinstance(pending, dict):
        return False
    if generation_stale_for_chat(behavior_store, user_id, group_id, generation):
        MONITOR.inc("turn_generation_stale_store_skip_total")
        return False
    sent = (assistant_text or "").strip()
    if not sent:
        return False
    user_payload = str(pending.get("user_payload") or "")
    dialogue_patch = dict(pending.get("dialogue_patch") or {})
    if not group_id:
        try:
            from core.prompt_routing import infer_assistant_expects_reply

            _ds = pending.get("ds_exec") if isinstance(pending.get("ds_exec"), dict) else {}
            dialogue_patch["assistant_expects_reply"] = infer_assistant_expects_reply(
                sent,
                task_tier=str(_ds.get("task_tier") or ""),
                last_intent=str(_ds.get("last_intent") or ""),
            )
        except Exception as e:
            logger.debug("pending expects_reply: %s", e)
    try:
        if pending.get("patch_session_task"):
            _pst = dict(pending["patch_session_task"])
            _pst["last_assistant_excerpt"] = sent[:480]
            behavior_store.patch_session_task(user_id, group_id, _pst)
    except Exception as e:
        logger.debug("pending patch_session_task: %s", e)
    try:
        rec, pending_dc = behavior_store.update_after_turn(
            user_id,
            group_id,
            user_payload,
            sent,
            dialogue_patch=dialogue_patch or None,
            group_patch=pending.get("group_patch") or None,
            blended_style=pending.get("blended_style"),
            micro_emotion=pending.get("micro_emotion"),
            telegram_is_admin=bool(pending.get("telegram_is_admin")),
            turn_meta=pending.get("turn_meta") or None,
        )
        if pending.get("pre_ctx_cdc_policy"):
            rec["cdc_policy"] = dict(pending["pre_ctx_cdc_policy"])
        if pending.get("pre_ctx_affect_state"):
            rec["affect_state"] = dict(pending["pre_ctx_affect_state"])
        if pending.get("pre_ctx_self_model"):
            rec["self_model"] = dict(pending["pre_ctx_self_model"])
        if pending.get("cdc_policy_patch"):
            rec["cdc_policy"] = dict(pending["cdc_policy_patch"])
        if goal_engine:
            goals_patch = goal_engine.update_after_turn(
                persisted=rec,
                user_text=user_payload,
                assistant_text=sent,
            )
            rec.update(goals_patch)
        try:
            from core.user_agent_impression import update_user_agent_impression_in_record

            update_user_agent_impression_in_record(
                rec,
                user_id=str(user_id),
                user_text=user_payload or "",
                telegram_is_admin=bool(pending.get("telegram_is_admin")),
            )
        except Exception as e:
            logger.debug("user_agent_impression pending: %s", e)
        st = rec.get("session_task")
        if isinstance(st, dict):
            st["last_tool"] = ""
            st["last_tool_ok"] = None
            st["last_tool_error"] = ""
        variant = str(pending.get("plan_fallback_variant") or "")
        if variant == "news_direct":
            _ndc = pending.get("news_digest_context")
            if isinstance(_ndc, dict):
                _ds_save = rec.get("dialogue_state")
                if not isinstance(_ds_save, dict):
                    _ds_save = {}
                    rec["dialogue_state"] = _ds_save
                if isinstance(_ndc.get("items"), list) and _ndc["items"]:
                    _ds_save["last_news_digest_items"] = _ndc["items"]
                if isinstance(_ndc.get("meta"), dict) and _ndc["meta"]:
                    _ds_save["last_news_digest_meta"] = _ndc["meta"]
        if sent and re.search(r"(?m)^\d+\.\s+\S", sent):
            try:
                from core.news_reply import stash_parsed_digest_from_assistant

                stash_parsed_digest_from_assistant(rec, sent)
            except Exception as e:
                logger.debug("stash_parsed_digest pending: %s", e)
        behavior_store.save(user_id, group_id, rec)
        if pending_dc and orchestrator is not None:
            try:
                from core.async_spawn import spawn_logged

                spawn_logged(
                    orchestrator._dialogue_compact_llm_apply(pending_dc),
                    label="dialogue_compact_llm",
                )
            except Exception as e:
                logger.debug("dialogue_compact spawn: %s", e)
        try:
            from core.message_archive import items_for_prompt
            from core.ops_trace import record_ops_turn

            _tm = pending.get("trace_meta") if isinstance(pending.get("trace_meta"), dict) else {}
            _ds = pending.get("ds_exec") if isinstance(pending.get("ds_exec"), dict) else {}
            _ch = str(_tm.get("channel") or _tm.get("source") or "telegram")
            _rs_ops: Dict[str, Any] = {}
            record_ops_turn(
                user_id=str(user_id),
                group_id=group_id,
                channel=_ch,
                user_text=user_payload or "",
                assistant_text=sent,
                recent_before=pending.get("recent_before") or [],
                recent_after=rec.get("recent_messages") or [],
                archive_tail=items_for_prompt(str(user_id), group_id),
                plan_steps=pending.get("plan_steps") or [],
                reasoning=_rs_ops,
                trace_id=str(_tm.get("trace_id") or "")[:64],
                latency_ms=int(_ds.get("total_latency_ms") or 0) if _ds else None,
                extra={
                    "profile": str(_ds.get("brain_profile") or _ds.get("router_profile") or ""),
                    "outcome": str(pending.get("outcome_all") or ""),
                },
            )
        except Exception as e:
            logger.debug("ops_trace pending: %s", e)
        MONITOR.inc("turn_store_after_delivery_total")
        return True
    except Exception as e:
        logger.debug("persist_turn_after_delivery: %s", e)
        return False


def finalize_delivery_from_output_meta(
    *,
    orchestrator: Any,
    user_id: str,
    group_id: Optional[str],
    sent_text: str,
    output_meta: Optional[Dict[str, Any]],
) -> bool:
    """Finalize pending store из output.meta после успешного send."""
    if not orchestrator or not user_id:
        return False
    meta = output_meta if isinstance(output_meta, dict) else {}
    pending = meta.get("pending_turn_store")
    if not isinstance(pending, dict):
        return False
    gen = 0
    try:
        gen = int(meta.get("turn_generation") or 0)
    except (TypeError, ValueError):
        gen = 0
    bs = getattr(orchestrator, "behavior_store", None)
    ge = getattr(orchestrator, "_goal_engine", None)
    return persist_turn_after_delivery(
        behavior_store=bs,
        goal_engine=ge,
        user_id=str(user_id),
        group_id=group_id,
        assistant_text=sent_text,
        pending=pending,
        generation=gen,
        orchestrator=orchestrator,
    )
