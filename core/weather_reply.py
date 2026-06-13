"""Погода: при weather_anchor — только lat/lon; иначе геокод + сохранение якоря после успеха."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from core.brain.text_helpers import (
    brain_weather_wttr_eager_fetch_enabled,
    looks_like_weather_meta_question,
    task_fact_profile,
    weather_city_country_resolve,
    weather_geo_query_for_api,
    weather_region_hint_resolve,
    weather_wttr_forecast_day_index,
)
from core.resilience import with_retry
from core.weather_location_store import (
    anchor_from_weather_resolved,
    apply_last_weather_report,
    apply_weather_anchor,
    merge_persisted_weather_anchor,
    read_last_weather_report,
    weather_anchor_conflicts_user_facts,
)

logger = logging.getLogger(__name__)


def weather_direct_reply_enabled() -> bool:
    try:
        from core.brain_own_turn import planner_direct_allowed

        return planner_direct_allowed("weather")
    except Exception:
        raw = (os.getenv("WEATHER_DIRECT_REPLY_ENABLED") or "false").strip().lower()
        return raw in {"1", "true", "yes", "on"}


def _user_facts_from_persisted(persisted: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(persisted, dict):
        return {}
    facts = persisted.get("user_facts")
    return facts if isinstance(facts, dict) else {}


def _recent_from_persisted(persisted: Optional[Dict[str, Any]]) -> Any:
    if not isinstance(persisted, dict):
        return None
    rm = persisted.get("recent_messages")
    if isinstance(rm, list) and rm:
        return rm
    return None


def _admin1_ru_label(admin1: str) -> str:
    a = (admin1 or "").lower().replace("ё", "е")
    if "minsk" in a or "минск" in a:
        return "Example Region"
    if "mogilev" in a or "могил" in a:
        return "Могилёвская область"
    if "grodno" in a or "гродн" in a:
        return "Гродненская область"
    if "brest" in a or "брест" in a:
        return "Брестская область"
    if "vitebsk" in a or "витеб" in a:
        return "Витебская область"
    if "gomel" in a or "гомел" in a:
        return "Гомельская область"
    return (admin1 or "").strip()


def _place_from_assistant_weather_line(text: str) -> str:
    low = (text or "").lower().replace("ё", "е")
    m = re.search(
        r"(?i)(?:погод\w*|weather).{0,40}?\bв\s+([^(\n,]{3,80})",
        text or "",
    )
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(?i)\bв\s+([а-яёa-z][а-яёa-z\-\s]{2,60}?)\s*\(", text or "")
    if m2:
        return m2.group(1).strip()
    if "лошиц" in low:
        return "Лошица (Минск)"
    if "миханович" in low:
        return "Springfield"
    return ""


def try_weather_meta_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    facts: Optional[Dict[str, Any]] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """Ответ на «с какого района погода» по последнему прогнозу, без нового геокода."""
    if not looks_like_weather_meta_question(user_text):
        return None
    report = read_last_weather_report(persisted)
    place = ""
    admin = ""
    source = ""
    if report:
        place = str(report.get("place_label") or report.get("geo_query") or "").strip()
        admin = _admin1_ru_label(str(report.get("admin1") or ""))
        source = str(report.get("source") or "").strip()
    if not place and isinstance(recent_dialogue, list):
        for row in reversed(recent_dialogue[-10:]):
            if not isinstance(row, dict) or str(row.get("role") or "") != "assistant":
                continue
            place = _place_from_assistant_weather_line(str(row.get("text") or ""))
            if place:
                break
    fc = str((facts or {}).get("city") or "").strip()
    if not place and fc:
        place = fc
        admin = _admin1_ru_label(
            weather_region_hint_resolve("", facts or {}, recent_dialogue) or "minsk"
        )
    if not place:
        return (
            "В памяти сессии нет привязки последнего прогноза к району. "
            "Напишите «погода в …» с названием населённого пункта — зафиксирую точку."
        )
    parts = [f"Последний прогноз в этом чате был для: {place}."]
    if admin:
        parts.append(f"Район/область по данным API: {admin}.")
    if source and source not in ("forecast", "saved"):
        parts.append(f"Источник: {source}.")
    if fc and "миханович" in fc.lower() and "лошиц" in place.lower():
        parts.append(
            f"В профиле указано: {fc} — это не совпадает с последним ответом; "
            "лучше переспросить «погода в Springfield»."
        )
    return " ".join(parts)


def _persist_weather_anchor(
    *,
    user_id: Optional[str],
    group_id: Optional[str],
    persisted: Optional[Dict[str, Any]],
    resolved: Any,
    facts: Optional[Dict[str, Any]] = None,
    geo_query: str = "",
) -> None:
    anchor = anchor_from_weather_resolved(resolved if isinstance(resolved, dict) else {})
    if facts and weather_anchor_conflicts_user_facts(facts, anchor):
        logger.info("[weather] skip anchor persist: conflicts user_facts city")
        return
    if not anchor or not user_id:
        return
    if isinstance(persisted, dict):
        merge_persisted_weather_anchor(persisted, anchor)
    try:
        from core.behavior_store import BehaviorStore

        store = BehaviorStore()
        apply_weather_anchor(store, str(user_id), group_id, anchor)
        if isinstance(resolved, dict):
            apply_last_weather_report(
                store,
                str(user_id),
                group_id,
                {
                    "geo_query": geo_query or str(resolved.get("name") or ""),
                    "place_label": str(resolved.get("name") or geo_query or ""),
                    "admin1": str(resolved.get("admin1") or resolved.get("region") or ""),
                    "source": "open_meteo",
                },
                persisted=persisted,
            )
    except Exception as e:
        logger.debug("persist weather_anchor: %s", e)


def _persist_weather_report_wttr(
    *,
    user_id: Optional[str],
    group_id: Optional[str],
    persisted: Optional[Dict[str, Any]],
    geo_query: str,
    summary: str,
) -> None:
    if not user_id or not geo_query:
        return
    place = _place_from_assistant_weather_line(summary) or geo_query
    try:
        from core.behavior_store import BehaviorStore

        apply_last_weather_report(
            BehaviorStore(),
            str(user_id),
            group_id,
            {
                "geo_query": geo_query,
                "place_label": place,
                "admin1": "",
                "source": "wttr.in",
            },
            persisted=persisted,
        )
    except Exception as e:
        logger.debug("persist weather_report wttr: %s", e)


async def try_weather_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Optional[str]:
    try:
        from core.brain_own_turn import brain_weather_api_enabled

        if not brain_weather_api_enabled():
            return None
    except Exception:
        if not weather_direct_reply_enabled():
            return None
    text = (user_text or "").strip()
    if not text:
        return None
    try:
        from core.user_facts import plain_text_requests_user_facts_identity

        if plain_text_requests_user_facts_identity(text):
            return None
    except Exception as e:
        logger.debug("weather skip user_facts_identity: %s", e)
    from core.turn_context import build_turn_context, prepare_persisted_for_weather

    facts = _user_facts_from_persisted(persisted)
    recent = _recent_from_persisted(persisted)
    if looks_like_weather_meta_question(text):
        return try_weather_meta_reply(
            text, persisted=persisted, facts=facts, recent_dialogue=recent
        )
    prepare_persisted_for_weather(
        persisted, facts, user_id=str(user_id) if user_id else None, group_id=group_id
    )
    tc = build_turn_context(text, persisted, recent_dialogue=recent)
    if not tc.is_weather:
        return None
    wc = tc.weather_city
    wco = tc.weather_country
    geo_q = tc.weather_geo_query
    admin1_hint = tc.weather_region_hint
    use_coords = tc.weather_use_coords
    prof = {
        "weather_lat": tc.weather_lat,
        "weather_lon": tc.weather_lon,
        "weather_label": tc.weather_label,
        "is_weather": True,
    }
    if not wc and not use_coords:
        wc, wco = weather_city_country_resolve(
            text, facts, recent,
        )
        geo_q = str(
            weather_geo_query_for_api(wc, wco, weather_region_hint_resolve(text, facts, recent))[0]
            or wc
        ).strip()
        admin1_hint = weather_region_hint_resolve(text, facts, recent)
        if not wc:
            return None
    _city_resolved = bool(prof.get("is_weather")) and bool(
        str(prof.get("weather_city") or "").strip() or prof.get("weather_use_coords")
    )
    if not _city_resolved:
        try:
            from core.heuristic_context_gate import should_run_shortcut_async

            _planner_ctx: Dict[str, Any] = {}
            if recent:
                _planner_ctx["recent_dialogue"] = recent
            _gr = await should_run_shortcut_async(
                "weather_direct",
                text,
                persisted=persisted,
                planner_context=_planner_ctx or None,
            )
            if not _gr.allowed:
                logger.info(
                    "[weather] gate blocked shortcut reason=%s text=%r",
                    _gr.reason,
                    text[:48],
                )
                return None
        except Exception as e:
            logger.debug("weather_direct gate: %s", e)
    try:
        from modules.external_apis.service import ExternalAPIService

        svc = ExternalAPIService()

        async def _fetch() -> Dict[str, Any]:
            if use_coords:
                return await svc.weather_or_fallback(
                    city=geo_q or wc,
                    country=wco,
                    admin1_hint=admin1_hint,
                    latitude=float(prof["weather_lat"]),
                    longitude=float(prof["weather_lon"]),
                    label=str(prof.get("weather_label") or wc),
                )
            return await svc.weather_or_fallback(
                city=geo_q,
                country=wco,
                admin1_hint=admin1_hint,
            )

        wr = await with_retry(_fetch, retries=1, timeout_sec=10.0, tag="weather_direct_open_meteo")
        if (
            not wr.get("configured")
            and not use_coords
            and wc
            and len(wc) >= 4
            and wc[-1].lower() in "её"
            and "no location" in str(wr.get("error", "")).lower()
        ):
            wr = await with_retry(
                lambda: svc.weather_or_fallback(
                    city=geo_q[:-1] if geo_q.endswith(wc[-1]) else wc[:-1],
                    country=wco,
                    admin1_hint=admin1_hint,
                ),
                retries=1,
                timeout_sec=10.0,
                tag="weather_direct_open_meteo_stem",
            )
        summary = str(wr.get("summary") or "").strip()
        if wr.get("configured") and summary:
            _persist_weather_anchor(
                user_id=user_id,
                group_id=group_id,
                persisted=persisted,
                resolved=wr.get("resolved"),
                facts=facts,
                geo_query=geo_q,
            )
            return summary
        if brain_weather_wttr_eager_fetch_enabled() and not use_coords:
            day_idx = weather_wttr_forecast_day_index(text)
            wttr = await with_retry(
                lambda: svc.wttr_in_eager_summary(geo_q, wco, forecast_day_index=day_idx),
                retries=1,
                timeout_sec=12.0,
                tag="weather_direct_wttr",
            )
            if isinstance(wttr, str) and wttr.strip():
                _persist_weather_report_wttr(
                    user_id=user_id,
                    group_id=group_id,
                    persisted=persisted,
                    geo_query=geo_q,
                    summary=wttr.strip(),
                )
                return wttr.strip()
        err = str(wr.get("error") or "").strip()
        if err:
            return (
                f"Не удалось получить прогноз для «{wc}»"
                + (f" ({wco})" if wco else "")
                + f": {err}. Уточните город или пришлите метку на карте."
            )
        return (
            f"Не удалось получить прогноз для «{wc}»"
            + (f" ({wco})" if wco else "")
            + ". Попробуйте позже."
        )
    except Exception as e:
        logger.warning("weather_direct_reply failed uid=%s: %s", user_id, e)
        return None


def try_weather_reply_sync(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Optional[str]:
    """Синхронная обёртка для orchestrator.plan."""
    import asyncio
    import concurrent.futures

    coro = try_weather_reply(
        user_text,
        persisted=persisted,
        user_id=user_id,
        group_id=group_id,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=28)
