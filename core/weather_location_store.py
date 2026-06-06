"""Якорь погоды: lat/lon в behavior — без повторного угадывания одноимённых НП."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_truthy(name: str, *, default: bool) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _valid_coords(lat: Any, lon: Any) -> Optional[Tuple[float, float]]:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return None
    return la, lo


def normalize_anchor(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    coords = _valid_coords(raw.get("latitude"), raw.get("longitude"))
    if not coords:
        return None
    la, lo = coords
    label = str(raw.get("label") or raw.get("name") or "").strip()
    admin1 = str(raw.get("admin1") or raw.get("region") or "").strip()
    return {
        "latitude": la,
        "longitude": lo,
        "label": label[:120],
        "admin1": admin1[:80],
        "source": str(raw.get("source") or "saved")[:40],
        "updated_at": str(raw.get("updated_at") or _now_iso()),
    }


def weather_anchor_conflicts_user_facts(facts: Any, anchor: Any) -> bool:
    """
    Якорь от прошлого неверного геокода (Лошица, Могилёвская) не должен перебивать user_facts.city.
    """
    if not isinstance(facts, dict) or not isinstance(anchor, dict):
        return False
    city = str(facts.get("city") or "").strip()
    if not city:
        return False
    label = str(anchor.get("label") or "").lower().replace("ё", "е")
    city_n = city.lower().replace("ё", "е")
    admin1 = str(anchor.get("admin1") or "").lower().replace("ё", "е")
    city_key = city_n.replace(" ", "")
    is_village_profile = "миханович" in city_key or "springfield" in city_key
    if is_village_profile:
        if "лошиц" in label or "lohic" in label:
            return True
        if "mogilev" in admin1 or "могил" in label:
            return True
        if ("минск" in label or "minsk" in admin1) and not (
            "миханович" in label or "springfield" in label
        ):
            return True
    if "минск" in city_n and "район" in city_n and is_village_profile:
        if "лошиц" in label:
            return True
    base = re.sub(r"^(?:а\.г\.|аг\.|агрогородок)\s+", "", city_n, flags=re.IGNORECASE).strip()
    if len(base) >= 4 and base.split(",")[0].strip()[:8] not in label.replace(" ", ""):
        if ("миханович" in base or "springfield" in base) and not (
            "миханович" in label or "springfield" in label
        ):
            return True
    return False


def read_weather_anchor(persisted: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(persisted, dict):
        return None
    wa = normalize_anchor(persisted.get("weather_anchor"))
    if wa:
        return wa
    ds = persisted.get("dialogue_state")
    if isinstance(ds, dict):
        tl = ds.get("last_telegram_location")
        if isinstance(tl, dict):
            coords = _valid_coords(tl.get("latitude"), tl.get("longitude"))
            if coords:
                la, lo = coords
                return normalize_anchor(
                    {
                        "latitude": la,
                        "longitude": lo,
                        "label": str(tl.get("display_name") or ""),
                        "source": "telegram_pin",
                        "updated_at": _now_iso(),
                    }
                )
    return None


def anchor_from_telegram(location: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    coords = _valid_coords(location.get("latitude"), location.get("longitude"))
    if not coords:
        return None
    la, lo = coords
    return normalize_anchor(
        {
            "latitude": la,
            "longitude": lo,
            "label": str(location.get("display_name") or ""),
            "source": "telegram_pin",
            "updated_at": _now_iso(),
        }
    )


def anchor_from_weather_resolved(resolved: Dict[str, Any], *, source: str = "forecast") -> Optional[Dict[str, Any]]:
    if not isinstance(resolved, dict):
        return None
    coords = _valid_coords(resolved.get("latitude"), resolved.get("longitude"))
    if not coords:
        return None
    la, lo = coords
    return normalize_anchor(
        {
            "latitude": la,
            "longitude": lo,
            "label": str(resolved.get("name") or ""),
            "admin1": str(resolved.get("admin1") or resolved.get("region") or ""),
            "source": source,
            "updated_at": _now_iso(),
        }
    )


def apply_weather_anchor(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    anchor: Optional[Dict[str, Any]],
) -> None:
    """Записать якорь в behavior JSON (все последующие «погода» — по координатам)."""
    norm = normalize_anchor(anchor)
    if not norm or not behavior_store or not user_id:
        return
    try:
        behavior_store.patch_session_fields(user_id, group_id, {"weather_anchor": norm})
    except AttributeError:
        try:
            rec = behavior_store.load(user_id, group_id)
            rec["weather_anchor"] = norm
            behavior_store.save(user_id, group_id, rec)
        except Exception as e:
            logger.debug("apply_weather_anchor save: %s", e)
    except Exception as e:
        logger.debug("apply_weather_anchor patch: %s", e)


async def _geocode_anchor_from_facts_async(
    city: str,
    country: str,
) -> Optional[Dict[str, Any]]:
    from core.brain.text_helpers import weather_geo_query_for_api, weather_region_hint_from_text
    from modules.external_apis.service import ExternalAPIService

    rh = weather_region_hint_from_text(city)
    geo_q, rh2 = weather_geo_query_for_api(city, country, rh)
    if not geo_q:
        return None
    svc = ExternalAPIService()
    wr = await svc.weather_or_fallback(city=geo_q, country=country, admin1_hint=rh2 or rh)
    if not isinstance(wr, dict) or not wr.get("configured"):
        return None
    resolved = wr.get("resolved")
    if not isinstance(resolved, dict):
        return None
    return anchor_from_weather_resolved(resolved, source="facts_commit")


def refresh_weather_anchor_from_facts(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    facts: Dict[str, Any],
) -> None:
    """
    После commit city/country в user_facts — один геокод → weather_anchor.
    Best-effort: внутри running loop — create_task, иначе asyncio.run.
    """
    if not _env_truthy("WEATHER_ANCHOR_ON_FACT_COMMIT", default=True):
        return
    if not behavior_store or not user_id or not isinstance(facts, dict):
        return
    city = str(facts.get("city") or "").strip()
    if not city:
        return
    country = str(facts.get("country") or "").strip()

    async def _run() -> None:
        try:
            anchor = await _geocode_anchor_from_facts_async(city, country)
            if anchor:
                apply_weather_anchor(behavior_store, user_id, group_id, anchor)
        except Exception as e:
            logger.debug("refresh_weather_anchor_from_facts: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        try:
            asyncio.run(_run())
        except Exception as e:
            logger.debug("refresh_weather_anchor_from_facts sync: %s", e)


def merge_persisted_weather_anchor(persisted: Dict[str, Any], anchor: Dict[str, Any]) -> Dict[str, Any]:
    norm = normalize_anchor(anchor)
    if norm:
        persisted["weather_anchor"] = norm
    return persisted


def clear_weather_anchor(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    persisted: Optional[Dict[str, Any]] = None,
) -> None:
    """Сброс неверного якоря (Лошица vs профиль Springfield)."""
    if isinstance(persisted, dict):
        persisted.pop("weather_anchor", None)
    if not behavior_store or not user_id:
        return
    try:
        behavior_store.patch_session_fields(user_id, group_id, {"weather_anchor": {}})
    except Exception:
        try:
            rec = behavior_store.load(user_id, group_id)
            rec.pop("weather_anchor", None)
            behavior_store.save(user_id, group_id, rec)
        except Exception as e:
            logger.debug("clear_weather_anchor: %s", e)


def normalize_weather_report(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    place = str(raw.get("place_label") or raw.get("geo_query") or "").strip()
    if not place:
        return None
    return {
        "geo_query": str(raw.get("geo_query") or place).strip()[:200],
        "place_label": place[:120],
        "admin1": str(raw.get("admin1") or "").strip()[:80],
        "source": str(raw.get("source") or "forecast")[:40],
        "updated_at": str(raw.get("updated_at") or _now_iso()),
    }


def read_last_weather_report(persisted: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(persisted, dict):
        return None
    return normalize_weather_report(persisted.get("weather_last_report"))


def apply_last_weather_report(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    report: Dict[str, Any],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> None:
    norm = normalize_weather_report(report)
    if not norm or not behavior_store or not user_id:
        return
    if isinstance(persisted, dict):
        persisted["weather_last_report"] = norm
    try:
        behavior_store.patch_session_fields(user_id, group_id, {"weather_last_report": norm})
    except Exception:
        try:
            rec = behavior_store.load(user_id, group_id)
            rec["weather_last_report"] = norm
            behavior_store.save(user_id, group_id, rec)
        except Exception as e:
            logger.debug("apply_last_weather_report: %s", e)


def invalidate_weather_anchor_if_conflicts(
    facts: Dict[str, Any],
    *,
    behavior_store: Any = None,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """True если якорь сброшен как несовместимый с user_facts.city."""
    anchor = read_weather_anchor(persisted)
    if not weather_anchor_conflicts_user_facts(facts, anchor):
        return False
    if user_id and behavior_store:
        clear_weather_anchor(behavior_store, str(user_id), group_id, persisted)
    elif isinstance(persisted, dict):
        persisted.pop("weather_anchor", None)
    return True
