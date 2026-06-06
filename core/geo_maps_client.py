"""
HTTP-клиент для карт/геокодинга: Nominatim, OSRM, статическая карта (OSM).
Выключается через GEO_MAPS_ENABLED=false. Соблюдайте политику Nominatim (лимит запросов).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_nominatim_lock = asyncio.Lock()
_last_nominatim_mono = 0.0


def geo_maps_enabled() -> bool:
    raw = os.getenv("GEO_MAPS_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _http_headers() -> Dict[str, str]:
    ua = (os.getenv("GEO_HTTP_USER_AGENT") or "gemma_bot/1.0 (+https://github.com/ManSio/gemma_agent)").strip()
    return {"User-Agent": ua, "Accept": "application/json"}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по сфере (км)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r * c


def point_in_polygon(lon: float, lat: float, ring: List[List[float]]) -> bool:
    """Один контур GeoJSON-порядка: список [lon, lat]."""
    if len(ring) < 3:
        return False
    inside = False
    n = len(ring)
    for i in range(n):
        j = (i - 1) % n
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        intersect = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        )
        if intersect:
            inside = not inside
    return inside


async def _nominatim_throttle() -> None:
    global _last_nominatim_mono
    min_gap = 1.05
    try:
        min_gap = max(1.05, float((os.getenv("NOMINATIM_MIN_INTERVAL_SEC") or "1.05").strip()))
    except ValueError:
        min_gap = 1.05
    async with _nominatim_lock:
        now = time.monotonic()
        wait = min_gap - (now - _last_nominatim_mono)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_nominatim_mono = time.monotonic()


async def reverse_geocode_nominatim(latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
    if not geo_maps_enabled():
        return None
    base = (os.getenv("NOMINATIM_REVERSE_URL") or "https://nominatim.openstreetmap.org/reverse").strip()
    await _nominatim_throttle()
    params = {
        "lat": str(latitude),
        "lon": str(longitude),
        "format": "json",
        "accept-language": "ru,en",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_http_headers()) as session:
            async with session.get(base, params=params) as resp:
                if resp.status != 200:
                    logger.debug("nominatim reverse %s", resp.status)
                    return None
                data = await resp.json()
    except Exception as e:
        logger.debug("nominatim reverse failed: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    return {
        "display_name": data.get("display_name"),
        "address": data.get("address") if isinstance(data.get("address"), dict) else {},
        "lat": latitude,
        "lon": longitude,
        "raw": {k: data.get(k) for k in ("osm_id", "osm_type", "place_id") if data.get(k) is not None},
    }


async def forward_search_nominatim(query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    if not geo_maps_enabled():
        return []
    q = (query or "").strip()
    if not q:
        return []
    base = (os.getenv("NOMINATIM_SEARCH_URL") or "https://nominatim.openstreetmap.org/search").strip()
    await _nominatim_throttle()
    params = {
        "q": q,
        "format": "json",
        "limit": str(max(1, min(limit, 10))),
        "accept-language": "ru,en",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_http_headers()) as session:
            async with session.get(base, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
    except Exception as e:
        logger.debug("nominatim search failed: %s", e)
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in data[:10]:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row.get("lat"))
            lon = float(row.get("lon"))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "display_name": row.get("display_name"),
                "latitude": lat,
                "longitude": lon,
                "importance": row.get("importance"),
            }
        )
    return out


async def nearby_search_nominatim(
    latitude: float, longitude: float, query: str, *, limit: int = 5
) -> List[Dict[str, Any]]:
    """Поиск POI рядом: bounded search по viewport."""
    if not geo_maps_enabled():
        return []
    q = (query or "").strip()
    if not q:
        return []
    deg = 0.08
    try:
        deg = max(0.01, min(0.5, float((os.getenv("GEO_NEARBY_VIEWPORT_DEG") or "0.08").strip())))
    except ValueError:
        deg = 0.08
    left, right = longitude - deg, longitude + deg
    bottom, top = latitude - deg, latitude + deg
    base = (os.getenv("NOMINATIM_SEARCH_URL") or "https://nominatim.openstreetmap.org/search").strip()
    await _nominatim_throttle()
    params = {
        "q": q,
        "format": "json",
        "limit": str(max(1, min(limit, 10))),
        "viewbox": f"{left},{top},{right},{bottom}",
        "bounded": "1",
        "accept-language": "ru,en",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_http_headers()) as session:
            async with session.get(base, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
    except Exception as e:
        logger.debug("nominatim nearby failed: %s", e)
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in data[:10]:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row.get("lat"))
            lon = float(row.get("lon"))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "display_name": row.get("display_name"),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(haversine_km(latitude, longitude, lat, lon), 3),
            }
        )
    out.sort(key=lambda x: float(x.get("distance_km") or 1e9))
    return out[: max(1, min(limit, 10))]


async def osrm_route_summary(
    from_latitude: float,
    from_longitude: float,
    to_latitude: float,
    to_longitude: float,
) -> Optional[Dict[str, Any]]:
    if not geo_maps_enabled():
        return None
    base = (os.getenv("OSRM_ROUTING_BASE") or "https://router.project-osrm.org").rstrip("/")
    # OSRM: lon,lat
    url = (
        f"{base}/route/v1/driving/{from_longitude},{from_latitude};"
        f"{to_longitude},{to_latitude}?overview=false&steps=false"
    )
    timeout = aiohttp.ClientTimeout(total=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": _http_headers()["User-Agent"]}) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception as e:
        logger.debug("osrm route failed: %s", e)
        return None
    if not isinstance(data, dict) or data.get("code") != "Ok":
        return None
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        return None
    r0 = routes[0]
    if not isinstance(r0, dict):
        return None
    dist = r0.get("distance")
    dur = r0.get("duration")
    try:
        km = float(dist) / 1000.0
        minutes = float(dur) / 60.0
    except (TypeError, ValueError):
        return None
    return {"distance_km": round(km, 3), "duration_min": round(minutes, 1), "profile": "driving"}


async def fetch_static_map_to_file(latitude: float, longitude: float, zoom: int = 14) -> Optional[str]:
    """Скачивает PNG статической карты; путь к временному файлу."""
    if not geo_maps_enabled():
        return None
    try:
        z = int(zoom)
    except (TypeError, ValueError):
        z = 14
    z = max(3, min(z, 18))
    try:
        w = max(200, min(1024, int((os.getenv("GEO_STATIC_MAP_WIDTH") or "640").strip())))
        h = max(200, min(1024, int((os.getenv("GEO_STATIC_MAP_HEIGHT") or "400").strip())))
    except ValueError:
        w, h = 640, 400
    tmpl = (
        os.getenv("GEO_STATIC_MAP_URL_TEMPLATE")
        or "https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom={z}&size={w}x{h}&maptype=mapnik"
    )
    url = tmpl.format(lat=latitude, lon=longitude, z=z, w=w, h=h)
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": _http_headers()["User-Agent"]}) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.debug("static map http %s", resp.status)
                    return None
                body = await resp.read()
    except Exception as e:
        logger.debug("static map fetch failed: %s", e)
        return None
    if not body or len(body) < 100:
        return None
    fd, path = tempfile.mkstemp(prefix="gemma_geo_", suffix=".png")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(body)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return path
