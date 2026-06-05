"""Разбор служебных маркеров [[loc:…]] / [[map:…]] в ответе чата."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

_LOC_RE = re.compile(r"\[\[loc:\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*\]\]", re.I)
_MAP_RE = re.compile(
    r"\[\[map:\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*(?:,\s*(\d+))?\s*\]\]",
    re.I,
)


async def expand_telegram_geo_placeholders(reply: str) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {}
    if not reply:
        return reply, meta
    m = _LOC_RE.search(reply)
    if m:
        try:
            meta["telegram_location_reply"] = {
                "latitude": float(m.group(1)),
                "longitude": float(m.group(2)),
            }
            reply = _LOC_RE.sub("", reply, count=1).strip()
        except ValueError:
            logger.debug("geo loc token parse failed")
    m2 = _MAP_RE.search(reply)
    if m2:
        try:
            lat, lon = float(m2.group(1)), float(m2.group(2))
            zoom = int(m2.group(3) or 14)
            from core.geo_maps_client import fetch_static_map_to_file

            path = await fetch_static_map_to_file(lat, lon, zoom=zoom)
            if path:
                meta["image_output_path"] = path
            reply = _MAP_RE.sub("", reply, count=1).strip()
        except ValueError:
            logger.debug("geo map token parse failed")
    return reply, meta
