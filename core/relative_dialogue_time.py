"""
Окна «вчера утром», «неделю назад», «в апреле», ISO-даты и т.п. для фильтрации архива переписки.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from core.timezone_inference import infer_timezone_from_facts

_DAYS_AGO = re.compile(r"(?ui)(\d+)\s*дн(?:я|ей)\s+назад")
_WEEK_AGO = re.compile(r"(?ui)недел(?:ю|и)\s+назад")
_TODAY = re.compile(r"(?ui)\bсегодня\b")
_YESTERDAY = re.compile(r"(?ui)\bвчера\b")
_DAY_BEFORE = re.compile(r"(?ui)\bпозавчера\b")
_PART = re.compile(r"(?ui)\b(утром|днём|днем|вечером|ночью)\b")

_EN_YEST = re.compile(r"(?ui)\byesterday\b")
_EN_TODAY = re.compile(r"(?ui)\btoday\b")
_EN_WEEK = re.compile(r"(?ui)\b(?:a|one)\s+week\s+ago\b")
_EN_DAYS = re.compile(r"(?ui)(\d+)\s*days?\s+ago")
_EN_MORNING = re.compile(r"(?ui)\b(?:in\s+the\s+)?morning\b")
_EN_AFTERNOON = re.compile(r"(?ui)\bafternoon\b")
_EN_EVENING = re.compile(r"(?ui)\bevening\b")
_EN_NIGHT = re.compile(r"(?ui)\b(?:at\s+)?night\b")

# Запрос про содержание прошлого диалога + относительное время
_DIALOG_MARKERS_RU = (
    "что ",
    "что?",
    "напомни",
    "писал",
    "писали",
    "обсужд",
    "говорил",
    "говорили",
    "было",
    "перепис",
    "сообщен",
    "диалог",
    "разговор",
    "истори",
    "вспомни",
)
_DIALOG_MARKERS_EN = ("what ", "recall", "remind", "discussed", "said", "history", "message")

_MONTH_STEMS_RU: Tuple[Tuple[str, int], ...] = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("мае", 5),
    ("мая", 5),
    ("май", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
)

_MONTH_STEMS_EN: Tuple[Tuple[str, int], ...] = (
    ("january", 1),
    ("february", 2),
    ("march", 3),
    ("april", 4),
    ("may", 5),
    ("june", 6),
    ("july", 7),
    ("august", 8),
    ("september", 9),
    ("october", 10),
    ("november", 11),
    ("december", 12),
)

_ISO_DAY = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def _month_num_from_text(text: str) -> Optional[int]:
    s = (text or "").lower()
    for stem, num in _MONTH_STEMS_RU:
        m = re.search(r"(?<![а-яё])" + re.escape(stem), s, flags=re.IGNORECASE)
        if m:
            return num
    for stem, num in _MONTH_STEMS_EN:
        m = re.search(r"(?<![a-z])" + re.escape(stem), s, flags=re.IGNORECASE)
        if m:
            return num
    return None


def _explicit_year_from_text(text: str) -> Optional[int]:
    m = re.search(r"\b(20\d{2})\b", text or "")
    if not m:
        return None
    try:
        y = int(m.group(1))
    except ValueError:
        return None
    return y if 2000 <= y <= 2099 else None


def _infer_year_for_month(month_num: int, ref_local: date) -> int:
    """Год календарного месяца без явной годовой метки (ориентир — дата сообщения)."""
    y = ref_local.year
    if ref_local.month == 1 and month_num >= 10:
        return y - 1
    if month_num > ref_local.month:
        return y
    if month_num < ref_local.month:
        return y
    return y


def parse_named_month_window_unix(
    text: str,
    *,
    user_facts: Dict[str, Any],
    reference_utc: datetime,
) -> Optional[Tuple[float, float, str]]:
    """
    Полный календарный месяц: «в апреле», «за апрель», «April», опционально «2026».
    Границы — в зоне пользователя, иначе UTC.
    """
    month_num = _month_num_from_text(text)
    if month_num is None:
        return None
    tz_name = str(user_facts.get("timezone") or "").strip() or infer_timezone_from_facts(user_facts) or None
    if tz_name and ZoneInfo:
        try:
            z = ZoneInfo(tz_name)
        except Exception:
            z = None
    else:
        z = None
    ref = reference_utc.astimezone(timezone.utc) if reference_utc.tzinfo else reference_utc.replace(tzinfo=timezone.utc)
    loc = ref.astimezone(z) if z else ref
    ref_d = loc.date()
    explicit_y = _explicit_year_from_text(text)
    if explicit_y is not None:
        year = explicit_y
    else:
        year = _infer_year_for_month(month_num, ref_d)
    try:
        start_d = date(year, month_num, 1)
        last_d = date(year, month_num, calendar.monthrange(year, month_num)[1])
    except ValueError:
        return None
    if z:
        start_l = datetime.combine(start_d, time(0, 0, 0), tzinfo=z)
        end_l = datetime.combine(last_d, time(0, 0, 0), tzinfo=z) + timedelta(days=1)
        start_u = start_l.astimezone(timezone.utc).timestamp()
        end_u = end_l.astimezone(timezone.utc).timestamp()
    else:
        start_l = datetime.combine(start_d, time(0, 0, 0), tzinfo=timezone.utc)
        end_l = datetime.combine(last_d, time(0, 0, 0), tzinfo=timezone.utc) + timedelta(days=1)
        start_u = start_l.timestamp()
        end_u = end_l.timestamp()
    label = f"month={year}-{month_num:02d}"
    if tz_name:
        label += f" tz={tz_name}"
    else:
        label += " tz=UTC(fallback)"
    return (float(start_u), float(end_u), label)


def parse_iso_day_window_unix(
    text: str,
    *,
    user_facts: Dict[str, Any],
    reference_utc: datetime,
) -> Optional[Tuple[float, float, str]]:
    """Одна дата YYYY-MM-DD в тексте — окно на этот календарный день (локально)."""
    m = _ISO_DAY.search(text or "")
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        day = date(y, mo, d)
    except ValueError:
        return None
    tz_name = str(user_facts.get("timezone") or "").strip() or infer_timezone_from_facts(user_facts) or None
    if tz_name and ZoneInfo:
        try:
            z = ZoneInfo(tz_name)
        except Exception:
            z = None
    else:
        z = None
    if z:
        start_l = datetime.combine(day, time(0, 0, 0), tzinfo=z)
        end_l = start_l + timedelta(days=1)
        start_u = start_l.astimezone(timezone.utc).timestamp()
        end_u = end_l.astimezone(timezone.utc).timestamp()
    else:
        start_l = datetime.combine(day, time(0, 0, 0), tzinfo=timezone.utc)
        end_l = start_l + timedelta(days=1)
        start_u = start_l.timestamp()
        end_u = end_l.timestamp()
    label = f"date={day.isoformat()}"
    if tz_name:
        label += f" tz={tz_name}"
    else:
        label += " tz=UTC(fallback)"
    return (float(start_u), float(end_u), label)


def parse_recall_time_window_unix(
    text: str,
    *,
    user_facts: Dict[str, Any],
    reference_utc: datetime,
) -> Optional[Tuple[float, float, str]]:
    """Сводный разбор: относительные дни → месяц по имени → ISO-день."""
    p = parse_relative_window_unix(text, user_facts=user_facts, reference_utc=reference_utc)
    if p:
        return p
    p2 = parse_named_month_window_unix(text, user_facts=user_facts, reference_utc=reference_utc)
    if p2:
        return p2
    return parse_iso_day_window_unix(text, user_facts=user_facts, reference_utc=reference_utc)


def recall_query_wants_earliest(text: str) -> bool:
    """Первая / самая ранняя реплика в окне (а не последние)."""
    s = (text or "").strip().lower()
    if not s:
        return False
    keys = (
        "перва",
        "первую",
        "первые",
        "раньше всех",
        "самая ран",
        "самую ран",
        "с начала",
        "любую перв",
        "любая перв",
        "earliest",
        "first message",
        "first record",
    )
    return any(k in s for k in keys)


def user_asks_relative_dialogue_time(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t) < 6:
        return False
    if not _has_relative_marker(text):
        return False
    if any(m in t for m in _DIALOG_MARKERS_RU) or any(m in t for m in _DIALOG_MARKERS_EN):
        return True
    if _month_num_from_text(t) is not None and (
        any(m in t for m in _DIALOG_MARKERS_RU)
        or any(m in t for m in _DIALOG_MARKERS_EN)
        or "?" in t
        or "запис" in t
        or "архив" in t
        or "поищ" in t
        or "найди" in t
    ):
        return True
    if "?" in t:
        return True
    # «вчера утром» / «неделю назад вечером» без явного «что писал»
    if _PART.search(t) and (_YESTERDAY.search(t) or _DAY_BEFORE.search(t) or _WEEK_AGO.search(t) or _DAYS_AGO.search(t)):
        return True
    return False


def _has_relative_marker(text: str) -> bool:
    s = text or ""
    if _TODAY.search(s) or _YESTERDAY.search(s) or _DAY_BEFORE.search(s):
        return True
    if _WEEK_AGO.search(s) or _DAYS_AGO.search(s):
        return True
    if _EN_YEST.search(s) or _EN_TODAY.search(s) or _EN_WEEK.search(s) or _EN_DAYS.search(s):
        return True
    return False


def _day_offset_from_text(text: str) -> Optional[int]:
    """Сколько дней назад от якорной локальной даты (0 = сегодня)."""
    s = text or ""
    if _WEEK_AGO.search(s) or _EN_WEEK.search(s):
        return 7
    m = _DAYS_AGO.search(s) or _EN_DAYS.search(s)
    if m:
        try:
            n = int(m.group(1))
            return max(1, min(120, n))
        except ValueError:
            return None
    if _DAY_BEFORE.search(s):
        return 2
    if _YESTERDAY.search(s) or _EN_YEST.search(s):
        return 1
    if _TODAY.search(s) or _EN_TODAY.search(s):
        return 0
    return None


def _daypart_from_text(text: str) -> Optional[Tuple[int, int]]:
    """Полуинтервал часов [h_start, h_end) в локальном дне. None = весь день."""
    s = text or ""
    m = _PART.search(s)
    if m:
        w = m.group(1).lower()
        if w == "утром":
            return (6, 12)
        if w in ("днём", "днем"):
            return (12, 18)
        if w == "вечером":
            return (18, 24)
        if w == "ночью":
            return (0, 6)
    if _EN_MORNING.search(s):
        return (6, 12)
    if _EN_AFTERNOON.search(s):
        return (12, 18)
    if _EN_EVENING.search(s):
        return (18, 24)
    if _EN_NIGHT.search(s):
        return (0, 6)
    return None


def parse_relative_window_unix(
    text: str,
    *,
    user_facts: Dict[str, Any],
    reference_utc: datetime,
) -> Optional[Tuple[float, float, str]]:
    """
    Возвращает (start_unix, end_unix, human_label) для полуинтервала [start, end).
    end не включается. reference_utc — обычно время сообщения Telegram (UTC aware).
    """
    off = _day_offset_from_text(text)
    if off is None:
        return None
    tz_name = str(user_facts.get("timezone") or "").strip() or infer_timezone_from_facts(user_facts) or None
    if tz_name and ZoneInfo:
        try:
            z = ZoneInfo(tz_name)
        except Exception:
            z = None
    else:
        z = None
    ref = reference_utc.astimezone(timezone.utc) if reference_utc.tzinfo else reference_utc.replace(tzinfo=timezone.utc)
    if z:
        loc = ref.astimezone(z)
    else:
        loc = ref
    d = loc.date() - timedelta(days=off)
    part = _daypart_from_text(text)
    if z:
        if part:
            h0, h1 = part
            start_l = datetime.combine(d, time(h0, 0, 0), tzinfo=z)
            if h1 >= 24:
                end_l = datetime.combine(d, time(0, 0, 0), tzinfo=z) + timedelta(days=1)
            else:
                end_l = datetime.combine(d, time(h1, 0, 0), tzinfo=z)
        else:
            start_l = datetime.combine(d, time(0, 0, 0), tzinfo=z)
            end_l = start_l + timedelta(days=1)
        start_u = start_l.astimezone(timezone.utc).timestamp()
        end_u = end_l.astimezone(timezone.utc).timestamp()
    else:
        # Нет пояса — границы в UTC по тем же часам (деградация)
        if part:
            h0, h1 = part
            start_l = datetime.combine(d, time(h0, 0, 0), tzinfo=timezone.utc)
            if h1 >= 24:
                end_l = start_l + timedelta(days=1)
            else:
                end_l = datetime.combine(d, time(h1, 0, 0), tzinfo=timezone.utc)
        else:
            start_l = datetime.combine(d, time(0, 0, 0), tzinfo=timezone.utc)
            end_l = start_l + timedelta(days=1)
        start_u = start_l.timestamp()
        end_u = end_l.timestamp()

    label = f"date={d.isoformat()}"
    if part:
        label += f" hours=[{part[0]},{part[1]})"
    if tz_name:
        label += f" tz={tz_name}"
    else:
        label += " tz=UTC(fallback)"
    return (float(start_u), float(end_u), label)


def merge_archive_and_recent_ts(
    archive_items: List[Dict[str, Any]],
    recent_messages: Optional[List[Any]],
) -> List[Dict[str, Any]]:
    """Объединяет архив и recent_dialogue, убирая дубликаты (telegram_ts, role)."""
    seen: set[Tuple[int, str]] = set()
    out: List[Dict[str, Any]] = []

    def _add(m: Dict[str, Any]) -> None:
        ts = m.get("telegram_ts")
        if ts is None:
            return
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            return
        role = str(m.get("role") or "").strip()
        key = (ts_i, role)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "role": role,
                "text": str(m.get("text") or ""),
                "telegram_ts": ts_i,
            }
        )

    for r in recent_messages or []:
        if isinstance(r, dict):
            _add(r)
    for m in archive_items:
        if isinstance(m, dict):
            _add(m)
    out.sort(key=lambda x: int(x.get("telegram_ts") or 0))
    return out


def filter_archive_items_by_unix_window(
    items: List[Dict[str, Any]],
    start_u: float,
    end_u: float,
    *,
    max_lines: int = 48,
    newest_first: bool = True,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        ts = m.get("telegram_ts")
        if ts is None:
            continue
        try:
            tsv = float(int(ts))
        except (TypeError, ValueError):
            continue
        if tsv < start_u or tsv >= end_u:
            continue
        out.append(m)
    out.sort(key=lambda x: int(x.get("telegram_ts") or 0))
    n = max(1, min(200, int(max_lines)))
    if newest_first:
        return out[-n:]
    return out[:n]


def format_relative_window_hint_lines(
    rows: List[Dict[str, Any]],
    *,
    label: str,
    clip: int = 220,
    picked_earliest: bool = False,
) -> str:
    if not rows:
        return (
            f"Относительное время: {label}.\n"
            "В архиве переписки (message_archive) нет реплик с telegram_ts в этом окне. "
            "Возможные причины: архив отключён, лимит DIALOGUE_MESSAGE_ARCHIVE_MAX слишком мал, "
            "сообщения без метки времени, или запрос относится к более старому периоду."
        )
    head = (
        f"Самые ранние реплики в окне ({label}). Используй только их; не выдумывай текст."
        if picked_earliest
        else f"Факты из архива переписки за окно ({label}). Используй только эти реплики; не выдумывай текст."
    )
    lines = [
        head,
        "Формат: [UTC время] роль: текст.",
    ]
    for m in rows:
        ts = m.get("telegram_ts")
        role = str(m.get("role") or "?").strip()
        try:
            utc_s = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            utc_s = str(ts)
        body = str(m.get("text") or "").replace("\n", " ").strip()
        if len(body) > clip:
            body = body[: clip - 1] + "…"
        lines.append(f"- {utc_s} {role}: {body}")
    return "\n".join(lines)
