from __future__ import annotations

import logging

from typing import Any, Dict, Optional

from core.llm_usage_store import aggregate_usage
from core.monitoring import MONITOR


logger = logging.getLogger(__name__)

def build_efficiency_snapshot(*, days: float = 7.0, orchestrator: Optional[Any] = None) -> Dict[str, Any]:
    d = max(1.0, min(float(days or 7.0), 365.0))
    agg = aggregate_usage(days=d)
    counters = MONITOR.snapshot().get("counters") if isinstance(MONITOR.snapshot(), dict) else {}
    ctr = counters if isinstance(counters, dict) else {}

    saved = int(ctr.get("auto_reasoning_est_saved_tokens_total", 0) or 0)
    baseline = int(ctr.get("auto_reasoning_est_baseline_tokens_total", 0) or 0)
    efficiency_pct = round((saved / baseline) * 100.0, 2) if baseline > 0 else 0.0

    m_total = int(ctr.get("module_exec_total", 0) or 0)
    m_ok = int(ctr.get("module_exec_ok_total", 0) or 0)
    m_fail = int(ctr.get("module_exec_fail_total", 0) or 0)
    m_success_pct = round((m_ok / m_total) * 100.0, 2) if m_total > 0 else 0.0

    p_total = int(ctr.get("planner_decisions_total", 0) or 0)
    p_fallback = int(ctr.get("planner_fallback_total", 0) or 0)
    p_success_pct = round(((p_total - p_fallback) / p_total) * 100.0, 2) if p_total > 0 else 0.0

    plugin_health: Dict[str, Any] = {"healthy": 0, "failed": 0, "disabled": 0, "total": 0}
    if orchestrator is not None and getattr(orchestrator, "plugin_registry", None) is not None:
        try:
            st = orchestrator.plugin_registry.get_system_state()
            mods = list(getattr(st, "modules", []) or [])
            plugin_health["total"] = len(mods)
            for m in mods:
                s = str(getattr(m, "status", "") or "").strip().lower()
                if s == "healthy":
                    plugin_health["healthy"] += 1
                elif s == "failed":
                    plugin_health["failed"] += 1
                else:
                    plugin_health["disabled"] += 1
        except Exception as e:
            logger.debug('%s optional failed: %s', 'efficiency_report', e, exc_info=True)
    return {
        "period_days": d,
        "llm": {
            "total_tokens": int(agg.get("total_tokens") or 0),
            "daily_avg_tokens": float(agg.get("daily_avg_tokens") or 0.0),
            "cost_sum": float(agg.get("cost_sum") or 0.0),
            "daily_avg_cost": float(agg.get("daily_avg_cost") or 0.0),
            "monthly_est_cost": float(agg.get("monthly_est_cost") or 0.0),
        },
        "token_saving": {
            "estimated_saved_tokens_total": saved,
            "estimated_baseline_tokens_total": baseline,
            "efficiency_percent": efficiency_pct,
        },
        "plugins": {
            "exec_total": m_total,
            "exec_ok": m_ok,
            "exec_fail": m_fail,
            "exec_success_percent": m_success_pct,
            "health": plugin_health,
        },
        "planner": {
            "decisions_total": p_total,
            "fallback_total": p_fallback,
            "route_success_percent": p_success_pct,
        },
    }
