"""
Естественный язык → повторяющееся расписание (еженедельно) в light_reminders.json.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from core.reminder_dispatch import add_recurring_reminder, _user_tz

logger = logging.getLogger(__name__)

_SCHEDULE_CUE_RE = re.compile(
    r"(?i)(?:"
    r"\bкаждый\b|\bкаждую\b|\bкаждое\b|\bпо\s+будням\b|\bпо\s+выходным\b|"
    r"\bеженедельн|\bрасписани\w*\s+на\s+недел"
    r")"
)

_WEEKDAY_MAP: Dict[str, int] = {
    "понедельник": 0,
    "пн": 0,
    "monday": 0,
    "mon": 0,
    "вторник": 1,
    "вт": 1,
    "tuesday": 1,
    "tue": 1,
    "сред": 2,
    "среда": 2,
    "ср": 2,
    "wednesday": 2,
    "wed": 2,
    "четверг": 3,
    "чт": 3,
    "thursday": 3,
    "thu": 3,
    "пятниц": 4,
    "пт": 4,
    "friday": 4,
    "fri": 4,
    "суббот": 5,
    "сб": 5,
    "saturday": 5,
    "sat": 5,
    "воскресен": 6,
    "вс": 6,
    "sunday": 6,
    "sun": 6,
}

_TIME_RE = re.compile(r"(?i)(?:в\s+)?(\d{1,2})[:.](\d{2})")


def schedule_nl_enabled() -> bool:
    raw = os.getenv("SCHEDULE_NL_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _extract_weekdays(text: str) -> Set[int]:
    low = (text or "").lower()
    dows: Set[int] = set()
    if "по будням" in low or "weekdays" in low:
        return {0, 1, 2, 3, 4}
    if "по выходным" in low or "weekends" in low:
        return {5, 6}
    if "каждый день" in low or "ежедневн" in low or "every day" in low:
        return set(range(7))
    for key, dow in _WEEKDAY_MAP.items():
        if key in low:
            dows.add(dow)
    return dows


def parse_weekly_schedule(text: str, *, user_id: str = "") -> Optional[Tuple[Set[int], int, int]]:
    """(dow set, hour, minute) или None."""
    raw = (text or "").strip()
    if not raw or not _SCHEDULE_CUE_RE.search(raw):
        return None
    dows = _extract_weekdays(raw)
    if not dows:
        return None
    m = _TIME_RE.search(raw)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return dows, h, mi


def _format_dows_ru(dows: Set[int]) -> str:
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    return ", ".join(names[d] for d in sorted(dows) if 0 <= d <= 6)


def extract_schedule_label(text: str) -> str:
    s = (text or "").strip()
    for pat in (
        r"(?i)^(?:ты\s+)?(?:можешь|может|нужно|надо)\s+",
        r"(?i)^(?:каждый|каждую|каждое)\s+день\s+",
        r"(?i)^(?:каждый|каждую|каждое)\s+\w+\s+",
        r"(?i)^по\s+будням\s+",
        r"(?i)^по\s+выходным\s+",
        r"(?i)^еженедельно\s+",
        r"(?i)^ежедневн\w*\s+",
    ):
        s = re.sub(pat, "", s, count=1)
    s = _TIME_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" .,;:-—")
    return s[:240] if len(s) >= 2 else "событие по расписанию"


def try_schedule_weekly_nl(user_id: str, text: str) -> Optional[Dict[str, Any]]:
    if not schedule_nl_enabled():
        return None
    uid = str(user_id or "").strip()
    raw = (text or "").strip()
    if not uid or not raw or raw.startswith("/"):
        return None
    parsed = parse_weekly_schedule(raw, user_id=uid)
    if not parsed:
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut

        if not should_run_shortcut("reminder_schedule", raw).allowed:
            return None
    except Exception as e:
        logger.debug("schedule_nl gate: %s", e)
    dows, h, mi = parsed
    label = extract_schedule_label(raw)
    try:
        from core.reminder_nl import _maybe_persist_user_tz

        _maybe_persist_user_tz(uid)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'schedule_nl', e, exc_info=True)
    rid = add_recurring_reminder(uid, label, dows=dows, hour=h, minute=mi)
    tz_name = _user_tz(uid)
    try:
        z = ZoneInfo(tz_name)
        when = f"{h:02d}:{mi:02d} ({tz_name}), дни: {_format_dows_ru(dows)}"
    except Exception:
        when = f"{h:02d}:{mi:02d}, дни: {_format_dows_ru(dows)}"
    freq = "ежедневное" if dows == set(range(7)) else "еженедельное"
    reply = (
        f"Ок, поставил {freq} напоминание: «{label}».\n"
        f"Буду напоминать {when}."
    )
    logger.info("[schedule_nl] recurring uid=%s id=%s dows=%s %02d:%02d", uid, rid, sorted(dows), h, mi)
    return {"ok": True, "reply": reply, "reminder_id": rid, "recurring": True}
