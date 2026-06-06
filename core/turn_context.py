"""
Единый контекст хода: профиль, слот, погода, якорь — одно место до shortcuts/brain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    user_text: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)
    recent_dialogue: List[Dict[str, Any]] = field(default_factory=list)
    persisted: Dict[str, Any] = field(default_factory=dict)
    is_weather: bool = False
    is_weather_meta: bool = False
    weather_city: str = ""
    weather_country: str = ""
    weather_geo_query: str = ""
    weather_region_hint: str = ""
    weather_use_coords: bool = False
    weather_lat: Optional[float] = None
    weather_lon: Optional[float] = None
    weather_label: str = ""
    weather_from_profile: bool = False


def build_turn_context(
    user_text: str,
    persisted: Optional[Dict[str, Any]] = None,
    *,
    recent_dialogue: Any = None,
) -> TurnContext:
    """Собрать контекст хода из behavior_store (одна точка для погоды и follow-up)."""
    rec = persisted if isinstance(persisted, dict) else {}
    facts = rec.get("user_facts") if isinstance(rec.get("user_facts"), dict) else {}
    recent: List[Dict[str, Any]] = []
    if isinstance(recent_dialogue, list):
        recent = [r for r in recent_dialogue if isinstance(r, dict)]
    elif isinstance(rec.get("recent_messages"), list):
        recent = [r for r in rec["recent_messages"] if isinstance(r, dict)]

    from core.brain.text_helpers import (
        looks_like_weather_meta_question,
        task_fact_profile,
        _user_text_looks_like_weather_query,
    )

    text = (user_text or "").strip()
    low = text.lower()
    is_meta = looks_like_weather_meta_question(text)
    is_wx = _user_text_looks_like_weather_query(low) and not is_meta

    tc = TurnContext(
        user_text=text,
        facts=dict(facts),
        recent_dialogue=recent,
        persisted=rec,
        is_weather=is_wx,
        is_weather_meta=is_meta,
    )
    if not is_wx:
        return tc

    prof = task_fact_profile(text, tc.facts, recent, persisted=rec)
    tc.weather_city = str(prof.get("weather_city") or "").strip()
    tc.weather_country = str(prof.get("weather_country") or "").strip()
    tc.weather_geo_query = str(prof.get("weather_geo_query") or tc.weather_city).strip()
    tc.weather_region_hint = str(prof.get("weather_region_hint") or "").strip()
    tc.weather_use_coords = bool(prof.get("weather_use_coords"))
    tc.weather_from_profile = bool(prof.get("weather_from_profile"))
    if prof.get("weather_lat") is not None:
        try:
            tc.weather_lat = float(prof["weather_lat"])
        except (TypeError, ValueError):
            pass
    if prof.get("weather_lon") is not None:
        try:
            tc.weather_lon = float(prof["weather_lon"])
        except (TypeError, ValueError):
            pass
    tc.weather_label = str(prof.get("weather_label") or "").strip()
    return tc


def prepare_persisted_for_weather(
    persisted: Optional[Dict[str, Any]],
    facts: Dict[str, Any],
    *,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
) -> None:
    """Сброс битого weather_anchor до любого запроса погоды."""
    try:
        from core.weather_location_store import invalidate_weather_anchor_if_conflicts

        store = None
        if user_id:
            from core.behavior_store import BehaviorStore

            store = BehaviorStore()
        invalidate_weather_anchor_if_conflicts(
            facts,
            behavior_store=store,
            user_id=str(user_id) if user_id else None,
            group_id=group_id,
            persisted=persisted if isinstance(persisted, dict) else None,
        )
    except Exception as e:
        logger.debug("prepare_persisted_for_weather: %s", e)
