from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.development_passport import get_development_passport


class GoalEngine:
    """Long-lived goals that influence behavior/planning hints."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)

    _DEFAULT_SOFT_GOAL_IDS = {"calm_structured_style", "fast_coding_help"}

    def _default_goals(self) -> List[Dict[str, Any]]:
        return [
            {"id": "calm_structured_style", "text": "keep style calm and structured", "status": "inactive", "weight": 0.6},
            {"id": "fast_coding_help", "text": "help user code faster", "status": "inactive", "weight": 0.7},
        ]

    def load_state(self, persisted: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {"goals": [], "active_topic": "", "updated_at": ""}
        p = persisted if isinstance(persisted, dict) else {}
        passport = get_development_passport()
        goals = p.get("goals_long_term")
        if not isinstance(goals, list) or not goals:
            goals = self._default_goals()
        else:
            # Normalise: дефолтные цели из persisted не должны быть active
            # (старый код мог сохранить их с status="active", что форсировало deep-профиль)
            for g in goals:
                if isinstance(g, dict) and str(g.get("id", "")).strip() in self._DEFAULT_SOFT_GOAL_IDS:
                    g["status"] = "inactive"
        return {
            "goals": goals,
            "active_topic": (p.get("topic_tracking") or {}).get("current", ""),
            "updated_at": p.get("goals_updated_at") or "",
            "mission": str(passport.get("mission") or ""),
            "evolution_vectors": list(passport.get("evolution_vectors") or []),
            "priorities": list(passport.get("priorities") or []),
            "stop_rules": list(passport.get("stop_rules") or []),
        }

    def update_after_turn(
        self,
        *,
        persisted: Dict[str, Any],
        user_text: str,
        assistant_text: str,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        state = self.load_state(persisted)
        goals = list(state["goals"])
        low = (user_text or "").lower()
        if any(k in low for k in ("code", "python", "refactor", "bug", "код")):
            self._upsert_goal(goals, "fast_coding_help", "help user code faster", 0.85)
        if any(k in low for k in ("stress", "tired", "устал", "сложно")):
            self._upsert_goal(goals, "emotional_comfort", "support emotional comfort", 0.9)
        return {
            "goals_long_term": goals,
            "goals_updated_at": datetime.now(timezone.utc).isoformat(),
            "last_goal_signal": (assistant_text or "")[:120],
        }

    def get_default_behavior_hints(self) -> Dict[str, Any]:
        """Return default soft goals as behavior hints, separate from active goals."""
        return {
            "default_hints": [
                {"id": "calm_structured_style", "text": "keep style calm and structured", "weight": 0.6},
                {"id": "fast_coding_help", "text": "help user code faster", "weight": 0.7},
            ],
        }

    def planning_hints(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {"active_goals": [], "goal_ids": []}
        goals = [g for g in (state.get("goals") or []) if isinstance(g, dict) and g.get("status") == "active"]
        top = sorted(goals, key=lambda g: float(g.get("weight", 0.0)), reverse=True)[:3]
        hints = self.get_default_behavior_hints()
        return {
            "active_goals": top,
            "goal_ids": [str(g.get("id")) for g in top],
            **hints,
            "mission": str(state.get("mission") or ""),
            "evolution_vectors": list(state.get("evolution_vectors") or []),
            "priorities": list(state.get("priorities") or []),
            "stop_rules": list(state.get("stop_rules") or []),
        }

    def _upsert_goal(self, goals: List[Dict[str, Any]], goal_id: str, text: str, weight: float) -> None:
        for g in goals:
            if str(g.get("id")) == goal_id:
                g["status"] = "active"
                g["weight"] = max(float(g.get("weight", 0.0)), float(weight))
                return
        goals.append({"id": goal_id, "text": text, "status": "active", "weight": float(weight)})
