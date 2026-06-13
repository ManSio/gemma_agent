"""Роутинг профиля brain: preflight → router → refine → continuation → audit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.brain.env import env_flag
from core.brain.runtime import _llm
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


@dataclass
class BrainRouteResolution:
    brain_profile: str
    heuristic_profile: str
    need_memory: bool
    router_result: Any = None
    route_preflight: Optional[str] = None
    route_continuation: str = ""
    situation_lane: str = ""
    classifier_result: Optional[Dict[str, Any]] = None


async def resolve_brain_route(
    user_text: str,
    context: Dict[str, Any],
    *,
    llm: Any = None,
) -> BrainRouteResolution:
    """
    Выбор профиля до сборки промпта. Побочный эффект: router_route_audit в context.
    """
    llm = llm if llm is not None else _llm
    ctx = context if isinstance(context, dict) else {}

    try:
        from core.brain.discourse_resolver import inherited_profile_from_context

        if ctx.get("_turn_state_collapsed") and isinstance(ctx.get("turn_state"), dict):
            _disc_profile = inherited_profile_from_context(ctx)
        else:
            from core.turn_reconcile import apply_discourse_and_collapse_async

            user_text, ctx = await apply_discourse_and_collapse_async(user_text, ctx, llm=llm)
            if isinstance(context, dict):
                context.update(ctx)
                ctx = context
            _disc_profile = inherited_profile_from_context(ctx)
    except Exception as e:
        logger.debug("discourse_resolver route: %s", e)
        _disc_profile = ""

    brain_profile = ""
    heuristic_profile = ""
    need_memory = False
    router_result: Optional[Any] = None
    route_preflight: Optional[str] = None
    route_continuation = ""

    try:
        from core.brain.profile_route_guard import preflight_profile
        from core.brain.profile_registry import is_valid_profile

        pre = preflight_profile(user_text)
        if pre and is_valid_profile(pre):
            route_preflight = pre
    except Exception as e:
        logger.debug("resolve_brain_route preflight: %s", e, exc_info=True)

    if route_preflight:
        try:
            from core.brain.router_classifier import ClassificationResult as ClsResult

            brain_profile = route_preflight
            heuristic_profile = brain_profile
            router_result = ClsResult(
                profile=route_preflight,
                confidence=1.0,
                source="preflight",
                latency_ms=0.0,
            )
        except Exception:
            brain_profile = route_preflight
            heuristic_profile = brain_profile
    else:
        try:
            from core.brain.router_classifier import classify as router_classify

            goal_hints = ctx.get("goal_hints") if isinstance(ctx.get("goal_hints"), dict) else {}
            active_goal_ids = [
                str(g.get("id"))
                for g in (goal_hints.get("active_goals") or [])
                if isinstance(g, dict) and g.get("id")
            ]
            router_result = await router_classify(
                user_text=user_text,
                llm=llm,
                active_goal_ids=active_goal_ids,
                intent_complexity=float(ctx.get("intent_complexity") or 0.0),
                context=ctx,
            )
            brain_profile = router_result.profile
            heuristic_profile = brain_profile
            need_memory = router_result.need_memory
        except Exception:
            logger.exception("[brain] router_classify failed, falling back to heuristic")
            from core.brain.agent_pack import determine_profile as fallback_determine
            from core.brain.profile_registry import refine_profile as refine_profile_fb

            ds_fb = ctx.get("dialogue_state") if isinstance(ctx.get("dialogue_state"), dict) else {}
            intent_fb = str(ds_fb.get("last_intent") or "") if isinstance(ds_fb, dict) else ""
            brain_profile = fallback_determine(user_text=user_text, context=ctx)
            brain_profile = refine_profile_fb(
                brain_profile,
                user_text,
                intent_fb,
                confidence=0.5,
                planner_context=ctx,
            )
            heuristic_profile = brain_profile

    try:
        from core.brain.profile_registry import refine_profile

        ds_early = ctx.get("dialogue_state") if isinstance(ctx.get("dialogue_state"), dict) else {}
        last_intent_early = str(ds_early.get("last_intent") or "") if isinstance(ds_early, dict) else ""
        router_conf_refine = float(router_result.confidence or 0.0) if router_result is not None else 1.0
        brain_profile = refine_profile(
            brain_profile,
            user_text,
            last_intent_early,
            confidence=router_conf_refine,
            planner_context=ctx,
        )
        try:
            from core.brain.dialogue_context import build_dsv

            dsv = build_dsv(ctx)
            if dsv.conflict_escalation >= 3:
                brain_profile = "short"
                need_memory = True
            elif dsv.correction_loop:
                brain_profile = "standard"
                need_memory = True
            elif (
                dsv.conflict_escalation >= 1
                and router_result is not None
                and float(router_result.confidence or 0.0) < 0.7
            ):
                brain_profile = "standard"
        except Exception as e:
            logger.debug("resolve_brain_route dsv: %s", e, exc_info=True)
    except Exception as e:
        logger.debug("resolve_brain_route refine: %s", e)

    classifier_result: Optional[Dict[str, Any]] = None
    try:
        from core.brain.classifier import classify_query

        classifier_result = await classify_query(user_text)
    except Exception as e:
        logger.debug("resolve_brain_route classifier: %s", e, exc_info=True)

    if classifier_result:
        from core.brain.profile_registry import (
            classifier_need_memory,
            merge_classifier_profile,
        )

        router_conf_merge = float(router_result.confidence or 0.0) if router_result is not None else 0.6
        brain_profile = merge_classifier_profile(
            brain_profile,
            classifier_result,
            router_confidence=router_conf_merge,
        )
        clf_nm = classifier_need_memory(classifier_result)
        if clf_nm is True:
            need_memory = True

    if router_result is not None and router_result.source == "batch_detector":
        try:
            from core.batch_continuation import (
                is_unified_problem,
                resolve_unified_problem_profile,
            )

            brain_profile = (
                resolve_unified_problem_profile(user_text)
                if is_unified_problem(user_text)
                else "batch"
            )
        except Exception:
            brain_profile = "batch"

    if ctx.get("brain_force_batch_profile"):
        brain_profile = "batch"

    try:
        from core.brain.profile_registry import resolve_continuation_profile

        cont_profile = resolve_continuation_profile(user_text, ctx)
        if cont_profile:
            brain_profile = cont_profile
            route_continuation = cont_profile
    except Exception as e:
        logger.debug("resolve_brain_route continuation: %s", e, exc_info=True)

    if _disc_profile and not route_preflight and not ctx.get("brain_force_batch_profile"):
        try:
            from core.brain.profile_registry import is_valid_profile

            if is_valid_profile(_disc_profile):
                brain_profile = _disc_profile
                route_continuation = _disc_profile
        except Exception as e:
            logger.debug("discourse profile inherit: %s", e)

    situation_lane = str(ctx.get("situation_lane") or "").strip()
    if situation_lane:
        try:
            from core.brain.profile_registry import get_profile

            get_profile(situation_lane)
            brain_profile = situation_lane
            MONITOR.inc("brain_situation_lane_total")
        except Exception as e:
            logger.debug("resolve_brain_route situation_lane: %s", e, exc_info=True)

    try:
        from core.brain.profile_route_guard import clamp_profile

        router_conf_final = (
            float(router_result.confidence or 0.0) if router_result is not None else 0.6
        )
        brain_profile = clamp_profile(
            brain_profile,
            user_text,
            router_confidence=router_conf_final,
        )
    except Exception as e:
        logger.debug("resolve_brain_route final clamp: %s", e, exc_info=True)

    try:
        from core.brain.profile_registry import build_route_audit

        _hg_audit = ctx.get("_heuristic_gate_audit")
        ra = build_route_audit(
            final_profile=brain_profile,
            preflight=route_preflight,
            router_profile=str(router_result.profile if router_result is not None else ""),
            router_source=str(router_result.source if router_result is not None else ""),
            router_confidence=float(router_result.confidence if router_result is not None else 0.0),
            continuation_profile=route_continuation,
            situation_lane=situation_lane,
            classifier_profile=str((classifier_result or {}).get("profile") or ""),
            heuristic_gate=_hg_audit if isinstance(_hg_audit, list) else None,
            discourse=ctx.get("discourse_audit") if isinstance(ctx.get("discourse_audit"), dict) else None,
        )
        try:
            from core.route_semantic_audit import build_semantic_audit_note

            _sa = build_semantic_audit_note(
                user_text=user_text,
                final_profile=brain_profile,
                classifier_profile=str((classifier_result or {}).get("profile") or ""),
                classifier_confidence=float((classifier_result or {}).get("confidence") or 0.0),
                router_source=str(router_result.source if router_result is not None else ""),
            )
            if _sa:
                ra["semantic_audit"] = _sa
        except Exception as e:
            logger.debug("semantic_audit: %s", e)
        ctx["router_route_audit"] = ra
        ctx["brain_profile"] = brain_profile
        ds_ra = ctx.setdefault("dialogue_state", {})
        if isinstance(ds_ra, dict):
            ds_ra["router_route_audit"] = ra
            ds_ra["brain_profile"] = brain_profile
    except Exception as e:
        logger.debug("resolve_brain_route audit: %s", e)

    return BrainRouteResolution(
        brain_profile=brain_profile,
        heuristic_profile=heuristic_profile,
        need_memory=need_memory,
        router_result=router_result,
        route_preflight=route_preflight,
        route_continuation=route_continuation,
        situation_lane=situation_lane,
        classifier_result=classifier_result,
    )


def log_brain_route(resolution: BrainRouteResolution, user_text: str) -> None:
    if not env_flag("MODEL_PROFILE_LOG", default=True):
        return
    router_result = resolution.router_result
    router_src = router_result.source if router_result is not None else "heuristic"
    router_conf = router_result.confidence if router_result is not None else 0.6
    router_lat = router_result.latency_ms if router_result is not None else 0.0
    clf_applied = "yes" if resolution.classifier_result else "no"
    clf_prof = (resolution.classifier_result or {}).get("profile", "None")
    logger.info(
        "[brain] profile=%s router=%s conf=%.2f classifier=%s clf_applied=%s len=%s lat=%.0fms",
        resolution.brain_profile,
        router_src,
        router_conf,
        clf_prof,
        clf_applied,
        len(user_text or ""),
        router_lat,
    )
