from __future__ import annotations

from typing import Any, Dict


class BehaviorEngine:
    """Long-horizon behavior policy engine (lightweight)."""

    def derive_policy(
        self,
        *,
        persona: Dict[str, Any],
        psychology: Dict[str, Any],
        user_facts: Dict[str, Any],
        dialogue_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        tone = "balanced"
        verbosity = "concise"
        if str((persona or {}).get("name", "")).lower().find("teacher") >= 0:
            tone = "didactic"
            verbosity = "structured"
        anxiety = (psychology or {}).get("anxiety_level")
        if anxiety == "high":
            tone = "supportive"
        age = user_facts.get("age")
        try:
            age_n = int(age) if age is not None else None
        except Exception:
            age_n = None
        audience = "general"
        if age_n is not None and age_n < 16:
            audience = "teen"
            verbosity = "structured"
        mode = (dialogue_state or {}).get("mode", "chat")
        return {
            "tone": tone,
            "verbosity": verbosity,
            "audience": audience,
            "mode": mode,
        }
