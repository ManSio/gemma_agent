"""Auto-reasoning plugin pack (opt-in via BRAIN_AUTO_REASONING_PLUGINS)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List

from core.brain.env import env_flag
from core.brain.text_helpers import safe_json_dumps
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


async def auto_reasoning_plugins_report(user_text: str) -> str:
    """
    Always-on lightweight reasoning stage.
    Runs a pack of deterministic plugins and returns compact JSON summary for LLM context.
    """
    if not env_flag("BRAIN_AUTO_REASONING_PLUGINS", default=False):
        return ""
    txt = (user_text or "").strip()
    if not txt:
        return ""
    try:
        from modules.ambiguity_detector.module import AmbiguityDetectorModule
        from modules.benchmark_runner.module import BenchmarkRunnerModule
        from modules.constraint_propagator.module import ConstraintPropagatorModule
        from modules.context_reducer.module import ContextReducerModule
        from modules.context_stability_monitor.module import ContextStabilityMonitorModule
        from modules.conflict_extractor.module import ConflictExtractorModule
        from modules.consistency_guard.module import ConsistencyGuardModule
        from modules.context_timeline.module import ContextTimelineModule
        from modules.error_memory.module import ErrorMemoryModule
        from modules.hidden_variable_finder.module import HiddenVariableFinderModule
        from modules.instruction_tracker.module import InstructionTrackerModule
        from modules.local_diff.module import LocalDiffModule
        from modules.local_math.module import LocalMathModule
        from modules.local_parser.module import LocalParserModule
        from modules.local_regex.module import LocalRegexModule
        from modules.local_text_ops.module import LocalTextOpsModule
        from modules.local_tokenizer.module import LocalTokenizerModule
        from modules.meta_reasoning_layer.module import MetaReasoningLayerModule
        from modules.minimal_model_builder.module import MinimalModelBuilderModule
        from modules.solution_explorer.module import SolutionExplorerModule
        from modules.state_machine_inspector.module import StateMachineInspectorModule
        from modules.symbol_counter.module import SymbolCounterModule
        from modules.task_classifier.module import TaskClassifierModule
        from modules.text_filter.module import TextFilterModule
        from modules.unicode_normalizer.module import UnicodeNormalizerModule
        from modules.self_check.module import SelfCheckModule
    except Exception as e:
        logger.debug("auto reasoning imports: %s", e)
        return ""

    async def _safe_call(name: str, coro):
        try:
            rep = await coro
            payload = str(getattr(rep, "payload", "") or "")
            parsed = {}
            try:
                parsed = json.loads(payload) if payload.strip().startswith("{") else {}
            except Exception:
                parsed = {}
            return {
                "plugin": name,
                "status": parsed.get("status") or "unknown",
                "confidence": parsed.get("confidence"),
                "matched_patterns": parsed.get("matched_patterns") if isinstance(parsed, dict) else None,
                "missed": parsed.get("missed") if isinstance(parsed, dict) else None,
            }
        except Exception as e:
            return {"plugin": name, "status": "error", "error": str(e)[:160]}

    try:
        timeout_sec = max(0.3, float((os.getenv("BRAIN_AUTO_REASONING_TIMEOUT_SEC") or "1.8").strip()))
    except ValueError:
        timeout_sec = 1.8

    # Step 1: task classification + meta strategy selection.
    strategy_names = ["local_text_ops", "context_reducer", "consistency_guard"]
    task_classes: List[str] = ["general_reasoning"]
    routing_policy = {
        "state_machine": ["state_machine_inspector", "consistency_guard", "minimal_model_builder"],
        "timeline": ["context_timeline", "conflict_extractor", "consistency_guard"],
        "symbolic_count": ["unicode_normalizer", "text_filter", "symbol_counter", "self_check"],
        "constraint_reasoning": ["consistency_guard", "hidden_variable_finder", "minimal_model_builder"],
        "ambiguity_sensitive": ["ambiguity_detector", "instruction_tracker", "consistency_guard"],
        "general_reasoning": ["local_text_ops", "local_parser", "local_tokenizer", "solution_explorer"],
    }
    try:
        cls_payload = await TaskClassifierModule().execute({"input": {"payload": f"/task_classify {txt}"}, "context": {}})
        cls_json = json.loads(str(getattr(cls_payload, "payload", "") or "{}"))
        cls_all = cls_json.get("task_classes")
        if isinstance(cls_all, list) and cls_all:
            task_classes = [str(x).strip() for x in cls_all if str(x).strip()]
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        mr_payload = await MetaReasoningLayerModule().execute({"input": {"payload": f"/meta_reason {txt}"}, "context": {}})
        mr_json = json.loads(str(getattr(mr_payload, "payload", "") or "{}"))
        cand = mr_json.get("selected_strategy")
        if isinstance(cand, list) and cand:
            strategy_names = [str(x).strip() for x in cand if str(x).strip()]
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # Deterministic route union from all detected classes + meta-selected strategy.
    deterministic_route: List[str] = []
    for cls in task_classes:
        deterministic_route.extend(routing_policy.get(cls, []))
    deterministic_route.extend(strategy_names)
    route_dedup: List[str] = []
    seen = set()
    for name in deterministic_route:
        n = str(name).strip()
        if n and n not in seen:
            seen.add(n)
            route_dedup.append(n)
    try:
        max_routed = max(3, int((os.getenv("BRAIN_AUTO_REASONING_MAX_ROUTED") or "8").strip()))
    except ValueError:
        max_routed = 8
    selected_route = route_dedup[:max_routed]

    core_jobs = [
        _safe_call("task_classifier", TaskClassifierModule().execute({"input": {"payload": f"/task_classify {txt}"}, "context": {}})),
        _safe_call("meta_reasoning_layer", MetaReasoningLayerModule().execute({"input": {"payload": f"/meta_reason {txt}"}, "context": {}})),
        _safe_call("ambiguity_detector", AmbiguityDetectorModule().execute({"input": {"payload": f"/ambiguity_detect {txt}"}, "context": {}})),
        _safe_call("error_memory", ErrorMemoryModule().execute({"input": {"payload": f"/error_memory check:{txt}"}, "context": {}})),
        _safe_call(
            "instruction_tracker",
            InstructionTrackerModule().execute({"input": {"payload": f"/instruction_track steps:{txt}; {txt} || answer:{txt}"}, "context": {}}),
        ),
        _safe_call("context_reducer", ContextReducerModule().execute({"input": {"payload": f"/context_reduce {txt}"}, "context": {}})),
    ]

    routed_jobs = []
    for name in selected_route:
        if name == "state_machine_inspector":
            routed_jobs.append(_safe_call(name, StateMachineInspectorModule().execute({"input": {"payload": f"/fsm_inspect {txt}"}, "context": {}})))
        elif name == "context_timeline":
            routed_jobs.append(_safe_call(name, ContextTimelineModule().execute({"input": {"payload": f"/timeline_build {txt}"}, "context": {}})))
        elif name == "consistency_guard":
            routed_jobs.append(_safe_call(name, ConsistencyGuardModule().execute({"input": {"payload": f"/consistency_guard conditions: {txt} || answer: {txt}"}, "context": {}})))
        elif name == "minimal_model_builder":
            routed_jobs.append(_safe_call(name, MinimalModelBuilderModule().execute({"input": {"payload": f"/minimal_model {txt}"}, "context": {}})))
        elif name == "hidden_variable_finder":
            routed_jobs.append(_safe_call(name, HiddenVariableFinderModule().execute({"input": {"payload": f"/hidden_vars constraints: {txt} || answer: {txt}"}, "context": {}})))
        elif name == "local_text_ops":
            routed_jobs.append(_safe_call(name, LocalTextOpsModule().execute({"input": {"payload": f"/local_text op=normalize_spaces || text={txt}"}, "context": {}})))
        elif name == "local_parser":
            routed_jobs.append(_safe_call(name, LocalParserModule().execute({"input": {"payload": '/local_parse fmt=json || text={"a":1}'}, "context": {}})))
        elif name == "local_diff":
            routed_jobs.append(_safe_call(name, LocalDiffModule().execute({"input": {"payload": "/local_diff a=abc || b=abd"}, "context": {}})))
        elif name == "local_math":
            routed_jobs.append(_safe_call(name, LocalMathModule().execute({"input": {"payload": "/local_math op=sum || nums=1,2,3"}, "context": {}})))
        elif name == "local_tokenizer":
            routed_jobs.append(_safe_call(name, LocalTokenizerModule().execute({"input": {"payload": f"/local_tokenize text={txt}"}, "context": {}})))
        elif name == "solution_explorer":
            routed_jobs.append(_safe_call(name, SolutionExplorerModule().execute({"input": {"payload": f"/solution_explorer {txt}"}, "context": {}})))
        elif name == "text_filter":
            routed_jobs.append(
                _safe_call(
                    name,
                    TextFilterModule().execute(
                        {"input": {"payload": f"/text_filter {txt} || rules=square,angle,after:#"}, "context": {}}
                    ),
                )
            )
        elif name == "symbol_counter":
            routed_jobs.append(_safe_call(name, SymbolCounterModule().execute({"input": {"payload": f"/symbol_count а || {txt} || rules=square,angle,after:#"}, "context": {}})))
        elif name == "self_check":
            routed_jobs.append(_safe_call(name, SelfCheckModule().execute({"input": {"payload": f"/self_check а || {txt} || 0 || rules=square,angle,after:#"}, "context": {}})))

    # Always-on low-cost hygiene checks.
    routed_jobs.extend(
        [
            _safe_call("unicode_normalizer", UnicodeNormalizerModule().execute({"input": {"payload": f"/unicode_normalize {txt}"}, "context": {}})),
            _safe_call("local_regex", LocalRegexModule().execute({"input": {"payload": f"/local_regex pattern=\\w+ || text={txt}"}, "context": {}})),
            _safe_call("constraint_propagator", ConstraintPropagatorModule().execute({"input": {"payload": f"/propagate_constraints {txt} || {txt}; {txt}"}, "context": {}})),
            _safe_call("context_stability_monitor", ContextStabilityMonitorModule().execute({"input": {"payload": f"/context_stability conditions:{txt} || steps:{txt}; {txt}"}, "context": {}})),
            _safe_call("conflict_extractor", ConflictExtractorModule().execute({"input": {"payload": f"/conflict_extract {txt}"}, "context": {}})),
            _safe_call("benchmark_runner", BenchmarkRunnerModule().execute({"input": {"payload": "/benchmark_run nosave"}, "context": {}})),
        ]
    )
    try:
        rows = await asyncio.wait_for(asyncio.gather(*(core_jobs + routed_jobs)), timeout=timeout_sec)
    except Exception as e:
        logger.debug("auto reasoning run: %s", e)
        return ""
    MONITOR.inc("auto_reasoning_runs_total")
    MONITOR.inc("auto_reasoning_plugins_total", len(rows))
    MONITOR.inc("auto_reasoning_routed_total", len(selected_route))
    local_plugin_names = {
        "local_text_ops",
        "local_regex",
        "local_math",
        "local_parser",
        "local_tokenizer",
        "local_diff",
        "text_filter",
        "symbol_counter",
        "self_check",
        "unicode_normalizer",
    }
    # Heuristic: each local deterministic call saves ~120 LLM tokens on average
    # versus asking the model to do the same micro-operation in-context.
    local_calls = len([r for r in rows if str(r.get("plugin") or "") in local_plugin_names and r.get("status") not in {"error", "invalid"}])
    est_saved_tokens = local_calls * 120
    baseline_llm_tokens = est_saved_tokens + 220
    MONITOR.inc("auto_reasoning_local_calls_total", local_calls)
    MONITOR.inc("auto_reasoning_est_saved_tokens_total", est_saved_tokens)
    MONITOR.inc("auto_reasoning_est_baseline_tokens_total", baseline_llm_tokens)
    ok = [r for r in rows if r.get("status") not in {"error", "invalid"}]
    bad = [r for r in rows if r.get("status") in {"error", "invalid"}]
    guard_rows = {str(r.get("plugin") or ""): r for r in rows}
    error_hits = len([x for x in (guard_rows.get("error_memory", {}) or {}).get("matched_patterns") or [] if x])
    missed_steps = len([x for x in (guard_rows.get("instruction_tracker", {}) or {}).get("missed") or [] if x])
    if error_hits:
        MONITOR.inc("auto_reasoning_error_memory_hits_total", error_hits)
    if missed_steps:
        MONITOR.inc("auto_reasoning_instruction_missed_total", missed_steps)
    compact = {
        "auto_reasoning_plugins": {
            "mode": "routed",
            "total": len(rows),
            "ok": len(ok),
            "issues": len(bad),
            "task_classes": task_classes,
            "selected_strategy": strategy_names,
            "selected_route": selected_route,
            "gates": {
                "error_memory_hits": error_hits,
                "instruction_missed_steps": missed_steps,
            },
            "efficiency": {
                "local_calls": local_calls,
                "estimated_saved_tokens": est_saved_tokens,
                "estimated_baseline_tokens": baseline_llm_tokens,
            },
            "results": rows[:14],
        }
    }
    return "AUTO_REASONING_PLUGIN_REPORT:\n" + safe_json_dumps(compact)


def extract_auto_reasoning_gates(report: str) -> Dict[str, int]:
    raw = str(report or "").strip()
    if not raw.startswith("AUTO_REASONING_PLUGIN_REPORT:"):
        return {"error_memory_hits": 0, "instruction_missed_steps": 0}
    try:
        body = raw.split("\n", 1)[1] if "\n" in raw else ""
        doc = json.loads(body) if body else {}
        ar = doc.get("auto_reasoning_plugins") if isinstance(doc.get("auto_reasoning_plugins"), dict) else {}
        gates = ar.get("gates") if isinstance(ar.get("gates"), dict) else {}
        err_hits = int(gates.get("error_memory_hits") or 0)
        missed = int(gates.get("instruction_missed_steps") or 0)
        return {
            "error_memory_hits": max(0, err_hits),
            "instruction_missed_steps": max(0, missed),
        }
    except Exception:
        return {"error_memory_hits": 0, "instruction_missed_steps": 0}


def _persona_apply_polished(user_id: str, reply: str, *, user_text: str = "") -> str:
    body = reply or ""
    if env_flag("BRAIN_STRIP_CHAT_MARKDOWN", default=True):
        body = _strip_chat_markdown_for_telegram(body)
    try:
        from core.brain.translation_path import is_translation_turn

        if is_translation_turn(user_text):
            return body
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    return _persona.apply_persona_to_response(user_id, body)


def _get_session_digest(user_id: str, group_id: Optional[str]) -> str:
    """Get stable session digest (≤ 300 chars) for prompt inclusion."""
    try:
        from core.session_digest import to_prompt_digest
        return to_prompt_digest(user_id, group_id)
    except Exception:
        return ""


