from __future__ import annotations

from typing import Any, Dict, List


class SelfImprovementAdvisor:
    """Advisory-only improvement detector. No auto-patch, no auto-exec."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)

    def suggest(
        self,
        *,
        diagnostics: Dict[str, Any],
        monitoring: Dict[str, Any],
    ) -> List[str]:
        if not self.enabled:
            return ["Self-improvement advisor is disabled by config."]
        out: List[str] = []
        by_component = (diagnostics or {}).get("by_component", {}) if isinstance(diagnostics, dict) else {}
        counters = (monitoring or {}).get("counters", {}) if isinstance(monitoring, dict) else {}
        if int(by_component.get("document_intake", 0)) > 10:
            out.append("DocumentIntake is noisy: add fallback parser and stricter input guard.")
        if int(by_component.get("skills", 0)) > 15:
            out.append("Skill failures are frequent: tighten per-skill validation and timeout policy.")
        if int(counters.get("planner_fallback_total", 0)) > 30:
            out.append("Planner fallback rate is elevated: refine intent-to-module mapping rules.")
        if int(counters.get("flood_blocked_total", 0)) > 50:
            out.append("High flood pressure: tune group cooldown preset for busy chats.")
        if not out:
            out.append("No critical hotspots detected; continue observability-driven tuning.")
        return out
