"""
Инструменты GeoMaps.* для мозга: геокодинг, маршрут, расстояние, POI, геозоны.
"""

from __future__ import annotations

from typing import Any, List, Optional

from core.geo_maps_client import (
    forward_search_nominatim,
    geo_maps_enabled,
    nearby_search_nominatim,
    osrm_route_summary,
    reverse_geocode_nominatim,
)
from core.geo_maps_client import haversine_km
from core.geo_zones_store import (
    zone_add_circle as _zone_add_circle,
    zone_add_polygon as _zone_add_polygon,
    zone_remove_last,
    zones_check,
    zones_list,
)


class GeoMapsModule:
    """Префикс инструментов GeoMaps для core/tools.py (класс *Module)."""

    BRAIN_LITE_INCLUDE = True

    async def reverse_geocode(self, latitude: float, longitude: float, **kwargs: Any) -> dict:
        if not geo_maps_enabled():
            return {"ok": False, "error": "GEO_MAPS_ENABLED=false"}
        r = await reverse_geocode_nominatim(float(latitude), float(longitude))
        if not r:
            return {"ok": False, "error": "reverse_geocode_failed"}
        return {"ok": True, "result": r}

    async def forward_geocode(self, query: str, **kwargs: Any) -> dict:
        if not geo_maps_enabled():
            return {"ok": False, "error": "GEO_MAPS_ENABLED=false"}
        lim = kwargs.get("limit", 5)
        try:
            lim_i = max(1, min(10, int(lim)))
        except (TypeError, ValueError):
            lim_i = 5
        rows = await forward_search_nominatim(str(query), limit=lim_i)
        return {"ok": True, "results": rows}

    async def nearby_search(self, latitude=None, longitude=None, query=None, **kwargs: Any) -> dict:
        if not geo_maps_enabled():
            return {"ok": False, "error": "GEO_MAPS_ENABLED=false"}
        if latitude is None or longitude is None or not query:
            return {"ok": False, "error": "location_required", "hint": "Pass latitude, longitude, and query."}
        lim = kwargs.get("limit", 5)
        try:
            lim_i = max(1, min(10, int(lim)))
        except (TypeError, ValueError):
            lim_i = 5
        rows = await nearby_search_nominatim(float(latitude), float(longitude), str(query), limit=lim_i)
        return {"ok": True, "results": rows}

    async def driving_route(
        self,
        from_latitude: float,
        from_longitude: float,
        to_latitude: float,
        to_longitude: float,
        **kwargs: Any,
    ) -> dict:
        if not geo_maps_enabled():
            return {"ok": False, "error": "GEO_MAPS_ENABLED=false"}
        r = await osrm_route_summary(
            float(from_latitude),
            float(from_longitude),
            float(to_latitude),
            float(to_longitude),
        )
        if not r:
            return {"ok": False, "error": "route_failed"}
        return {"ok": True, "route": r}

    async def distance_km(
        self,
        from_latitude: float,
        from_longitude: float,
        to_latitude: float,
        to_longitude: float,
        **kwargs: Any,
    ) -> dict:
        km = haversine_km(
            float(from_latitude),
            float(from_longitude),
            float(to_latitude),
            float(to_longitude),
        )
        return {"ok": True, "km": round(km, 3), "method": "haversine"}

    async def zone_add_circle(
        self,
        user_id: str,
        name: str,
        latitude: float,
        longitude: float,
        radius_km: float,
        **kwargs: Any,
    ) -> dict:
        return _zone_add_circle(str(user_id), str(name), float(latitude), float(longitude), float(radius_km))

    async def zone_add_polygon(self, user_id: str, name: str, coordinates: List[List[float]], **kwargs: Any) -> dict:
        return _zone_add_polygon(str(user_id), str(name), coordinates)

    async def zones_list(self, user_id: str, **kwargs: Any) -> dict:
        return {"ok": True, "zones": zones_list(str(user_id))}

    async def zones_check(self, user_id: str, latitude: float, longitude: float, **kwargs: Any) -> dict:
        return zones_check(str(user_id), float(latitude), float(longitude))

    async def zone_remove(self, user_id: str, **kwargs: Any) -> dict:
        name: Optional[str] = kwargs.get("name")
        if name is not None:
            name = str(name).strip() or None
        return zone_remove_last(str(user_id), name=name)
