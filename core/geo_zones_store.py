"""Пользовательские геозоны (круг / полигон), JSON в RESILIENCE_RUNTIME_DIR."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.geo_maps_client import haversine_km, point_in_polygon

_lock = threading.Lock()


def _path() -> Path:
    base = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
    return base / "geo_zones.json"


def _load_raw() -> Dict[str, List[Dict[str, Any]]]:
    p = _path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = [x for x in v if isinstance(x, dict)]
    return out


def _save_raw(data: Dict[str, List[Dict[str, Any]]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(p)


def zones_list(user_id: str) -> List[Dict[str, Any]]:
    with _lock:
        data = _load_raw()
        return list(data.get(str(user_id), []))


def zone_add_circle(user_id: str, name: str, latitude: float, longitude: float, radius_km: float) -> Dict[str, Any]:
    rec = {
        "name": str(name).strip() or "zone",
        "kind": "circle",
        "center_lat": float(latitude),
        "center_lon": float(longitude),
        "radius_km": float(radius_km),
    }
    with _lock:
        data = _load_raw()
        uid = str(user_id)
        data.setdefault(uid, []).append(rec)
        _save_raw(data)
    return {"ok": True, "zone": rec}


def zone_add_polygon(user_id: str, name: str, coordinates: List[List[float]]) -> Dict[str, Any]:
    if not coordinates or len(coordinates) < 3:
        return {"ok": False, "error": "polygon needs at least 3 [lon, lat] points"}
    ring: List[List[float]] = []
    for pt in coordinates:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ring.append([float(pt[0]), float(pt[1])])
    if len(ring) < 3:
        return {"ok": False, "error": "invalid coordinates"}
    rec = {"name": str(name).strip() or "zone", "kind": "polygon", "ring": ring}
    with _lock:
        data = _load_raw()
        uid = str(user_id)
        data.setdefault(uid, []).append(rec)
        _save_raw(data)
    return {"ok": True, "zone": rec}


def zones_check(user_id: str, latitude: float, longitude: float) -> Dict[str, Any]:
    hits: List[str] = []
    with _lock:
        zones = list(_load_raw().get(str(user_id), []))
    for z in zones:
        name = str(z.get("name") or "zone")
        kind = str(z.get("kind") or "")
        if kind == "circle":
            try:
                clat = float(z.get("center_lat"))
                clon = float(z.get("center_lon"))
                r = float(z.get("radius_km"))
            except (TypeError, ValueError):
                continue
            if haversine_km(latitude, longitude, clat, clon) <= r + 1e-6:
                hits.append(name)
        elif kind == "polygon":
            ring = z.get("ring")
            if isinstance(ring, list) and point_in_polygon(float(longitude), float(latitude), ring):
                hits.append(name)
    return {"inside": hits, "count": len(hits)}


def zone_remove_last(user_id: str, name: Optional[str] = None) -> Dict[str, Any]:
    with _lock:
        data = _load_raw()
        uid = str(user_id)
        lst = data.get(uid)
        if not lst:
            return {"ok": False, "error": "no zones"}
        if name:
            nm = str(name).strip()
            new = [z for z in lst if str(z.get("name") or "") != nm]
            if len(new) == len(lst):
                return {"ok": False, "error": "zone not found"}
            data[uid] = new
        else:
            data[uid] = lst[:-1]
        _save_raw(data)
    return {"ok": True}
