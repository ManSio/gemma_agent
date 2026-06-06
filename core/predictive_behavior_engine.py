from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


class PredictiveBehaviorEngine:
    """Lightweight heuristic predictor for next-step user needs."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)

    def predict(
        self,
        *,
        text: str,
        recent_dialogue: List[Dict[str, Any]],
        topic_tracking: Dict[str, Any],
        psychology: Dict[str, Any],
        user_facts: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"skill_priority": [], "terse_mode": False, "confidence": 0.0, "signals": {"enabled": False}}
        low = (text or "").lower()
        hour = datetime.now().hour
        programmer_bias = 0.0
        finance_bias = 0.0
        terse_mode = False

        if any(k in low for k in ("code", "python", "bug", "refactor", "ошибка", "код")):
            programmer_bias += 0.45
        if any(k in low for k in ("finance", "budget", "валют", "курс", "инвест")):
            finance_bias += 0.45
        if any(
            k in low
            for k in (
                "почему", "зачем", "отчего", "объясни", "расскажи", "что такое",
                "как работает", "как устроен",
            )
        ):
            terse_mode = False
        elif len((text or "").strip()) <= 14:
            if not any(
                k in low
                for k in (
                    "объясни", "расскажи", "почему", "простыми словами", "подробн",
                    "теорем", "урок", "пифагор", "продолж",
                )
            ):
                terse_mode = True

        for row in recent_dialogue[-8:]:
            t = str((row or {}).get("text", "")).lower()
            if any(k in t for k in ("code", "python", "bug", "ошибка", "код")):
                programmer_bias += 0.08
            if any(k in t for k in ("finance", "валют", "курс", "инвест")):
                finance_bias += 0.08

        if psychology.get("anxiety_level") == "high":
            terse_mode = True
        if 23 <= hour or hour < 7:
            terse_mode = True

        active_topic = str((topic_tracking or {}).get("current", "")).lower()
        if "код" in active_topic or "code" in active_topic:
            programmer_bias += 0.1
        if "финанс" in active_topic or "finance" in active_topic:
            finance_bias += 0.1

        confidence = min(0.95, round(max(programmer_bias, finance_bias) + (0.12 if terse_mode else 0.0), 3))
        skill_priority = []
        if programmer_bias >= 0.35:
            skill_priority.append("programmer")
        if finance_bias >= 0.35:
            skill_priority.append("finance")
        if user_facts.get("interests") and isinstance(user_facts.get("interests"), list):
            if "programming" in [str(x).lower() for x in user_facts.get("interests", [])]:
                if "programmer" not in skill_priority:
                    skill_priority.append("programmer")

        return {
            "skill_priority": skill_priority,
            "terse_mode": terse_mode,
            "confidence": confidence,
            "signals": {
                "programmer_bias": round(programmer_bias, 3),
                "finance_bias": round(finance_bias, 3),
                "night_hours": bool(23 <= hour or hour < 7),
            },
        }
