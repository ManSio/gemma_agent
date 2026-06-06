"""Снимки stage_ms и decision_trace для turns.jsonl (фаза B наблюдаемости)."""
from __future__ import annotations

from typing import Any, Dict, Optional


def build_decision_trace(
    *,
    planner_bypass: Optional[str],
    planner_reason: str,
    router_route_audit: Any,
    profile: str,
    module: str,
    last_tool: str,
    fallback_used: bool,
) -> Dict[str, Any]:
    ra = router_route_audit if isinstance(router_route_audit, dict) else {}
    trace: Dict[str, Any] = {
        "pre_llm_variant": (planner_bypass or None),
        "planner_reason": (planner_reason or None),
        "orchestrator_module": (module or None),
        "router_profile_initial": ra.get("router_profile"),
        "router_profile_final": ra.get("final_profile") or profile,
        "profile_guard_action": None,
        "shortcut_rule_id": None,
        "gate_verdict": None,
        "fallback_used": bool(fallback_used),
        "last_tool": (last_tool or None),
    }
    sa = ra.get("semantic_audit")
    if isinstance(sa, dict) and sa.get("mismatch"):
        trace["profile_guard_action"] = "semantic_mismatch"
    hg = ra.get("heuristic_gate")
    if isinstance(hg, list) and hg:
        last = hg[-1] if isinstance(hg[-1], dict) else {}
        if last.get("shortcut_rule_id"):
            trace["shortcut_rule_id"] = str(last.get("shortcut_rule_id"))
        if last.get("gate_verdict"):
            trace["gate_verdict"] = str(last.get("gate_verdict"))
    return {k: v for k, v in trace.items() if v is not None}


def stage_ms_for_trace_id(trace_id: str) -> Optional[Dict[str, int]]:
    if not (trace_id or "").strip():
        return None
    try:
        from core.observability import OBS

        return OBS.stage_ms_snapshot(str(trace_id).strip())
    except Exception:
        return None
