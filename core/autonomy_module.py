from __future__ import annotations

import json
import os
from typing import Any, Dict, List


class AutonomyModule:
    """Advisory-only autonomy layer. No code execution."""

    def __init__(self) -> None:
        base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join(os.getcwd(), "data"))
        self.log_path = os.path.join(base, "runtime_errors.jsonl")

    def _read_events(self, limit: int = 400) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.log_path):
            return []
        rows: List[Dict[str, Any]] = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows[-limit:]

    def auto_suggestions(self, **kwargs) -> List[str]:
        _ = kwargs
        rows = self._read_events()
        msgs = [r.get("message", "") for r in rows]
        out: List[str] = []
        if sum(1 for m in msgs if "anti_flood" in m or "message blocked" in m) > 20:
            out.append("Increase MAX_MSG_PER_10S for busy groups or refine GROUP_COOLDOWN_SEC.")
        if sum(1 for m in msgs if "tool returned error" in m) > 10:
            out.append("Improve tool error handling and add retries/fallback for unstable tools.")
        if sum(1 for m in msgs if "link" in m.lower()) > 5:
            out.append("Enable stricter LINK_SAFETY_MODE in public groups.")
        if not out:
            out.append("System looks stable; consider adding a domain-specific skill for common intents.")
        return out

    def auto_diagnostics(self, **kwargs) -> Dict[str, Any]:
        _ = kwargs
        rows = self._read_events(limit=800)
        by_component: Dict[str, int] = {}
        by_message: Dict[str, int] = {}
        for r in rows:
            comp = str(r.get("component", "unknown"))
            msg = str(r.get("message", "unknown"))
            by_component[comp] = by_component.get(comp, 0) + 1
            by_message[msg] = by_message.get(msg, 0) + 1
        top_messages = sorted(by_message.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "events_total": len(rows),
            "by_component": by_component,
            "top_messages": top_messages,
        }

    def auto_optimize_hints(self, **kwargs) -> List[str]:
        _ = kwargs
        diag = self.auto_diagnostics()
        hints: List[str] = []
        by_comp = diag.get("by_component", {})
        if by_comp.get("anti_flood", 0) > 50:
            hints.append("Consider anti-flood preset for busy groups and higher GROUP_COOLDOWN_SEC.")
        if by_comp.get("external_apis", 0) > 20:
            hints.append("Increase API timeout/retries and add cached fallback responses.")
        if by_comp.get("skills", 0) > 20:
            hints.append("Stabilize skill handlers with stricter input validation and per-skill timeout.")
        if by_comp.get("image_tools", 0) > 15:
            hints.append("Lower IMAGE_MAX_RESOLUTION for weak hardware to reduce memory pressure.")
        if not hints:
            hints.append("No heavy hotspots detected; keep monitoring and add domain skills incrementally.")
        return hints

    def idea(self, topic: str = "", **kwargs) -> Dict[str, Any]:
        _ = kwargs
        t = (topic or "").strip() or "general"
        return {
            "topic": t,
            "proposal": f"Design a new optional skill '{t}_assistant' with strict input/output contracts and env toggles.",
            "safety": "advisory_only_no_auto_execution",
        }
