"""
Psychology Engine Module — эвристический профиль пользователя (персистентно на диске).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.getcwd(), "data", "psychology_profiles.json")


class PsychologyEngineModule:
    """Модуль психологического анализа с сохранением между перезапусками."""

    def __init__(self, storage_path: Optional[str] = None) -> None:
        self._path = storage_path or os.getenv("PSYCHOLOGY_PROFILES_PATH", _DEFAULT_PATH)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self._path):
            self.profiles = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.profiles = raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning("psychology_engine load failed: %s", e)
            self.profiles = {}

    def _save(self) -> None:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception as e:
            logger.warning("psychology_engine save failed: %s", e)

    def get_psychology_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        uid = str(user_id)
        return self.profiles.get(uid)

    def analyze_message(self, user_id: str, message: str) -> Dict[str, Any]:
        from core.utils.llm_sanitize import sanitize_llm_value
        message = sanitize_llm_value(message)
        uid = str(user_id)
        text = message or ""
        low = text.lower()
        stress_hits = any(
            w in low
            for w in (
                "тревож",
                "устал",
                "стресс",
                "выгор",
                "бессон",
                "депрес",
                "страшно",
                "паник",
                "надоел",
            )
        )
        analysis: Dict[str, Any] = {
            "user_id": uid,
            "message_length": len(text),
            "sentiment": "stressed" if stress_hits else "neutral",
            "stress_signals": stress_hits,
            "keywords": _extract_context_keywords(low),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        prev = dict(self.profiles.get(uid) or {})
        prev["last_analysis"] = analysis
        prev["updated_at"] = analysis["analyzed_at"]
        streak = int(prev.get("stress_streak") or 0)
        if stress_hits:
            streak = min(streak + 1, 50)
        else:
            streak = max(0, streak - 1)
        prev["stress_streak"] = streak
        self.update_profile(uid, prev)
        return analysis

    def update_profile(self, user_id: str, data: Dict[str, Any]) -> bool:
        uid = str(user_id)
        if uid not in self.profiles:
            self.profiles[uid] = {}
        self.profiles[uid].update(data)
        self._save()
        return True


def _extract_context_keywords(low: str) -> list:
    tags: list[str] = []
    if re.search(r"\bучеб|работ|проект|дедлайн", low):
        tags.append("work_study")
    if re.search(r"сон|сплю|не сплю", low):
        tags.append("sleep")
    if re.search(r"семь|родител|дет", low):
        tags.append("family")
    if re.search(r"одиноч|груст|тоск", low):
        tags.append("mood_low")
    return tags[:8]
