"""Прямой ответ на геометку Telegram (без LLM и без «не понял»)."""
from __future__ import annotations

import logging

import re
from typing import Any, Dict, Optional, Tuple

from core.geo_maps_client import geo_maps_enabled

_LOCATION_SYNTHETIC_RE = re.compile(
    r"(?i)пользователь\s+прислал\s+метку\s+карты|telegram\s+location"
)


logger = logging.getLogger(__name__)

def is_telegram_location_intro_text(text: str) -> bool:
    """Синтетический текст input_layer при геометке (не запрос «что рядом»)."""
    return bool(_LOCATION_SYNTHETIC_RE.search(text or ""))


def is_telegram_location_turn(
    meta: Optional[Dict[str, Any]],
    user_text: str = "",
) -> bool:
    if isinstance(meta, dict) and isinstance(meta.get("telegram_location"), dict):
        try:
            float(meta["telegram_location"]["latitude"])
            float(meta["telegram_location"]["longitude"])
            return True
        except (KeyError, TypeError, ValueError):
            pass
    return bool(_LOCATION_SYNTHETIC_RE.search(user_text or ""))


def format_telegram_location_reply(
    meta: Optional[Dict[str, Any]],
    *,
    user_text: str = "",
) -> str:
    tl = (meta or {}).get("telegram_location") if isinstance(meta, dict) else None
    if not isinstance(tl, dict):
        return ""
    try:
        lat = float(tl["latitude"])
        lon = float(tl["longitude"])
    except (KeyError, TypeError, ValueError):
        return ""
    disp = str(tl.get("display_name") or "").strip()
    lines = ["Принял вашу точку на карте."]
    if disp:
        lines.append(f"Место: {disp}")
    else:
        lines.append(f"Координаты: {lat:.5f}, {lon:.5f}")
    if not geo_maps_enabled():
        lines.append(
            "Геосервисы отключены (GEO_MAPS_ENABLED=false). "
            "Могу ответить по названию места текстом."
        )
    else:
        lines.append(
            "Могу подсказать: напишите «погода здесь», «что рядом», "
            "«маршрут до …» или «где я» — использую эту точку."
        )
    low = (user_text or "").lower()
    if "погод" in low:
        lines.append("Для погоды напишите: погода здесь.")
    return "\n".join(lines).strip()


def try_telegram_location_reply_sync(
    user_text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not is_telegram_location_turn(meta, user_text):
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut

        if not should_run_shortcut(
            "telegram_location_ack",
            user_text or "",
            meta=meta if isinstance(meta, dict) else None,
        ).allowed:
            return None
    except Exception as e:
        logger.debug("telegram_location_ack gate: %s", e)
    try:
        from core.geo_nearby_reply import is_explicit_nearby_request

        if is_explicit_nearby_request(user_text):
            return None
    except Exception as e:
        logger.debug('%s optional failed: %s', 'geo_location_reply', e, exc_info=True)
    body = format_telegram_location_reply(meta, user_text=user_text)
    return body if body.strip() else None
