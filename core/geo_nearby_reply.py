"""Прямой ответ «что рядом» без LLM и без JSON-схем в чат."""
from __future__ import annotations

import logging

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.geo_maps_client import geo_maps_enabled, haversine_km, nearby_search_nominatim


logger = logging.getLogger(__name__)

# «рядом с ним/зубом» — описание соседства объектов, не карта.
_RELATIONAL_RYADOM_RE = re.compile(
    r"(?i)рядом\s+с\s+(?:ним|ней|ними|другом|друг\s+другом|этим|тем|одним|одной|"
    r"сосед\w*|зуб\w*|пломб\w*|коронк\w*|имплант\w*)"
)

_EXPLICIT_NEARBY_GEO_RE = re.compile(
    r"(?i)"
    r"(?:^|[\.\?\!]\s*)(?:что\s+(?:есть\s+)?рядом|где\s+рядом)|"
    r"рядом\s+со\s+мной|рядом\s+с\s+мной|"
    r"около\s+меня|поблизости|near\s+me|what'?s\s+nearby|"
    r"(?:кафе|аптек\w*|магазин\w*|остановк\w*|банкомат\w*|метро|кофейн\w*)\s+рядом|"
    r"рядом\s+(?:кафе|аптек\w*|магазин\w*|остановк\w*|банкомат\w*|метро)"
)

_GEO_TOPIC_MARKERS_RE = re.compile(
    r"(?i)геометк|/geo_help|координат|широт[аы]|долгот[аы]|"
    r"маршрут\s+до|как\s+доехать|дорог[аи]\s+до|"
    r"(?:какая\s+)?погод(?:а|у|е)\s+(?:здесь|сейчас|сегодня|завтра)"
)


def is_relational_ryadom(text: str) -> bool:
    return bool(_RELATIONAL_RYADOM_RE.search(text or ""))


def is_nearby_request(text: str) -> bool:
    return is_explicit_nearby_request(text)


def is_geo_topic_context(text: str, *, has_location_attachment: bool = False) -> bool:
    """
    Тема сообщения — карта/погода/маршрут (для situation_playbook), без ложного «рядом с зубом».
    """
    if has_location_attachment:
        return True
    if is_explicit_nearby_request(text):
        return True
    low = (text or "").strip().lower()
    if not low:
        return False
    if is_relational_ryadom(low) and not _EXPLICIT_NEARBY_GEO_RE.search(low):
        return False
    return bool(_GEO_TOPIC_MARKERS_RE.search(low))


def is_explicit_nearby_request(text: str) -> bool:
    """Запрос «что рядом», а не «рядом с ним» в описании ситуации (зубы, предметы)."""
    try:
        from core.geo_location_reply import is_telegram_location_intro_text

        if is_telegram_location_intro_text(text):
            return False
    except Exception as e:
        logger.debug('%s optional failed: %s', 'geo_nearby_reply', e, exc_info=True)
    low = (text or "").strip().lower()
    if len(low) < 4:
        return False
    if low in {"рядом", "что рядом", "что рядом?", "около", "поблизости"}:
        return True
    if is_relational_ryadom(low):
        return False
    return bool(_EXPLICIT_NEARBY_GEO_RE.search(low))


def _parse_nearby_categories(text: str) -> List[str]:
    low = (text or "").lower()
    cats: List[str] = []
    if re.search(r"кафе|кофе|coffee", low):
        cats.append("кафе")
    if re.search(r"аптек|pharmacy", low):
        cats.append("аптека")
    if re.search(r"магазин|shop|супермаркет|продукт", low):
        cats.append("супермаркет")
    if re.search(r"остановк|транспорт|метро|bus", low):
        cats.append("остановка общественного транспорта")
    if re.search(r"банкомат|atm", low):
        cats.append("банкомат")
    if not cats:
        cats = ["кафе", "аптека", "супермаркет"]
    return cats[:4]


def _short_name(display_name: str, max_len: int = 72) -> str:
    s = (display_name or "").strip()
    if not s:
        return "объект"
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if parts:
        s = parts[0]
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _location_from_context(
    meta: Optional[Dict[str, Any]],
    persisted: Optional[Dict[str, Any]],
) -> Optional[Tuple[float, float]]:
    if isinstance(meta, dict):
        tl = meta.get("telegram_location")
        if isinstance(tl, dict):
            try:
                return float(tl["latitude"]), float(tl["longitude"])
            except (KeyError, TypeError, ValueError):
                pass
    if isinstance(persisted, dict):
        ds = persisted.get("dialogue_state")
        if isinstance(ds, dict):
            tl2 = ds.get("last_telegram_location")
            if isinstance(tl2, dict):
                try:
                    return float(tl2["latitude"]), float(tl2["longitude"])
                except (KeyError, TypeError, ValueError):
                    pass
    return None


async def try_geo_nearby_reply(
    user_text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not is_nearby_request(user_text):
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut_async

        _gr = await should_run_shortcut_async(
            "geo_nearby",
            user_text,
            meta=meta,
            persisted=persisted,
        )
        if not _gr.allowed:
            return None
    except Exception as e:
        logger.debug("geo_nearby gate: %s", e)
    if not geo_maps_enabled():
        return (
            "Геопоиск сейчас отключён (GEO_MAPS_ENABLED=false). "
            "Могу подсказать по названию места, если напишете адрес текстом."
        )
    loc = _location_from_context(meta, persisted)
    if loc is None:
        return (
            "Чтобы показать, что рядом, нужна точка на карте. "
            "Нажмите /geo_help и отправьте геометку — затем снова «что рядом»."
        )
    lat, lon = loc
    try:
        lim = int((os.getenv("GEO_NEARBY_REPLY_LIMIT") or "5").strip())
    except ValueError:
        lim = 5
    lim = max(3, min(lim, 8))
    seen: set[str] = set()
    lines: List[str] = []
    for q in _parse_nearby_categories(user_text):
        rows = await nearby_search_nominatim(lat, lon, q, limit=lim)
        for row in rows:
            name = _short_name(str(row.get("display_name") or ""))
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                d_km = haversine_km(lat, lon, float(row["latitude"]), float(row["longitude"]))
                dist = f" (~{d_km:.1f} км)" if d_km < 50 else ""
            except (TypeError, ValueError, KeyError):
                dist = ""
            lines.append(f"• {name}{dist}")
            if len(lines) >= 12:
                break
        if len(lines) >= 12:
            break
    if not lines:
        return (
            "По открытым картам в этой зоне ничего не нашлось по базовым категориям. "
            "Уточните: «кафе рядом», «аптека рядом» или пришлите другую геометку."
        )
    head = "Рядом с вашей точкой:"
    return head + "\n" + "\n".join(lines[:12])


def try_geo_nearby_reply_sync(
    user_text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Синхронная обёртка для orchestrator.plan (внутри уже запущенного event loop)."""
    import asyncio
    import concurrent.futures

    coro = try_geo_nearby_reply(user_text, meta=meta, persisted=persisted)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=28)
