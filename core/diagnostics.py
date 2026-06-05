from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from core.data_governance import DG
from core.development_passport import get_development_passport
from core.error_analysis import aggregate_error_stats
from core.monitoring import MONITOR
from core.observability import OBS
from core.self_improvement_advisor import SelfImprovementAdvisor
from core.host_resources import get_host_resource_snapshot
from core.plugin_requirements import (
    INSTALL_POLICY,
    INSTALL_POLICY_DETAIL,
    iter_plugin_manifest_roots,
    merge_plugin_requirements_report,
)
from core.connectivity_check import get_external_connectivity_hints_for_health


def _plugin_dependencies_block() -> Dict[str, Any]:
    report = merge_plugin_requirements_report()
    return {
        "install_policy": INSTALL_POLICY,
        "install_policy_detail": INSTALL_POLICY_DETAIL,
        "runtime_pip_install_forbidden": True,
        "pip_requirements_by_module": report.by_module,
        "pip_merged": report.merged_lines,
        "pip_merge_hints": report.hints,
        "pip_duplicate_distribution_keys": report.duplicate_distribution_keys,
        "manifest_roots_scanned": [str(p) for p in iter_plugin_manifest_roots()],
    }


def build_diagnostic_snapshot(orchestrator: Any = None) -> Dict[str, Any]:
    info = orchestrator.get_system_info() if orchestrator and hasattr(orchestrator, "get_system_info") else {}
    mon = MONITOR.snapshot()
    counters = mon.get("counters", {}) if isinstance(mon, dict) else {}
    advisor_enabled = True
    if isinstance(info, dict):
        advisor_enabled = bool((info.get("advisor") or {}).get("enabled", True))
    advisor = SelfImprovementAdvisor(enabled=advisor_enabled)
    err_stats = aggregate_error_stats(limit=500)
    passport = get_development_passport()
    kpi_targets = passport.get("kpi_targets") if isinstance(passport, dict) else {}
    if not isinstance(kpi_targets, dict):
        kpi_targets = {}
    kpi_eval = {
        "planner_fallback_ok": int(counters.get("planner_fallback_total", 0)) <= int(kpi_targets.get("planner_fallback_total_max", 10**9)),
        "security_high_risk_ok": int(counters.get("security_high_risk_total", 0)) <= int(kpi_targets.get("security_high_risk_total_max", 10**9)),
        "flood_blocked_ok": int(counters.get("flood_blocked_total", 0)) <= int(kpi_targets.get("flood_blocked_total_max", 10**9)),
    }
    # Только snapshot: evaluate() резильенса вызывает build_diagnostic_snapshot — не вызывать evaluate здесь.
    resilience: Dict[str, Any] = {}
    if orchestrator is not None and hasattr(orchestrator, "_resilience"):
        rc = getattr(orchestrator, "_resilience")
        try:
            resilience = {"snapshot": rc.snapshot()}
        except Exception as e:
            resilience = {"error": str(e)}

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "system": info,
        "monitoring": mon,
        "observability": OBS.snapshot(),
        "errors": err_stats,
        "governance": {
            "retention_days_logs": DG.retention_days_logs,
            "retention_days_behavior": DG.retention_days_behavior,
            "redact_keys": sorted(DG.redact_keys),
        },
        "knowledge": {
            "rows_ingested_total": int(counters.get("knowledge_rows_ingested_total", 0)),
            "policy_fresh_total": int(counters.get("knowledge_hint_policy_fresh_total", 0)),
        },
        "security": {
            "flood_blocked_total": int(counters.get("flood_blocked_total", 0)),
            "link_flagged_total": int(counters.get("link_safety_flagged_total", 0)),
            "link_dangerous_total": int(counters.get("link_safety_dangerous_total", 0)),
            "security_warning_total": int(counters.get("security_warning_total", 0)),
            "security_high_risk_total": int(counters.get("security_high_risk_total", 0)),
        },
        "advisor": {
            "mode": "advisory_only",
            "suggestions": advisor.suggest(diagnostics=err_stats, monitoring=mon),
        },
        "development_passport": {
            "mission": str(passport.get("mission") or ""),
            "evolution_vectors": list(passport.get("evolution_vectors") or []),
            "priorities": list(passport.get("priorities") or []),
            "kpi_targets": kpi_targets,
            "kpi_eval": kpi_eval,
            "stop_rules": list(passport.get("stop_rules") or []),
        },
        "plugin_dependencies": _plugin_dependencies_block(),
        "resilience": resilience,
        # Не вызывать build_unified_health_snapshot здесь: evaluate() резильенса уже
        # вызывает build_diagnostic_snapshot — вложенный unified health даёт глубокую рекурсию и «зависание» /admin_system.
        "host_resources": get_host_resource_snapshot(),
        "external_services": get_external_connectivity_hints_for_health(),
    }
