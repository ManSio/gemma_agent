"""
Единый часовой пояс для логов и админ-отчётов (не для хранения данных — там по-прежнему UTC).

Переменные окружения (первое ненулевое):
  GEMMA_REPORT_TIMEZONE — предпочтительно, напр. Europe/Minsk
  GEMMA_LOG_TIMEZONE
  LOG_TIMEZONE

Пусто / UTC / GMT / Z — всё в UTC (как раньше).
"""
from __future__ import annotations

import os
import warnings
from datetime import datetime, timezone
from typing import Any, Optional


def get_report_tz():
    """tzinfo для отображения: ZoneInfo или timezone.utc."""
    raw = (
        os.getenv("GEMMA_REPORT_TIMEZONE")
        or os.getenv("GEMMA_LOG_TIMEZONE")
        or os.getenv("LOG_TIMEZONE")
        or "UTC"
    ).strip()
    if not raw or raw.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(raw)
    except Exception as e:
        # Нельзя вызывать logging.* отсюда: get_report_tz() дергается из Formatter.formatTime → рекурсия.
        warnings.warn(
            f"GEMMA_REPORT_TIMEZONE={raw!r} недоступен ({e}), используем UTC",
            UserWarning,
            stacklevel=2,
        )
        return timezone.utc


def _raw_tz_env() -> str:
    return (
        os.getenv("GEMMA_REPORT_TIMEZONE")
        or os.getenv("GEMMA_LOG_TIMEZONE")
        or os.getenv("LOG_TIMEZONE")
        or ""
    ).strip()


def report_timezone_label() -> str:
    """Короткая подпись для UI (имя зоны или UTC)."""
    raw = _raw_tz_env()
    return raw if raw else "UTC"


def report_utc_offset_label() -> str:
    """Человекочитаемое смещение относительно UTC (напр. UTC+3 для Europe/Minsk)."""
    if report_time_uses_utc_wall():
        return "UTC+0"
    sample = datetime.now(timezone.utc).astimezone(get_report_tz())
    off = sample.utcoffset()
    if off is None:
        return report_timezone_label()
    sec = int(off.total_seconds())
    sign = "+" if sec >= 0 else "-"
    sec = abs(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    if m:
        return f"UTC{sign}{h}:{m:02d}"
    return f"UTC{sign}{h}"


def report_time_uses_utc_wall() -> bool:
    """True — вести себя как раньше (все метки UTC)."""
    raw = _raw_tz_env()
    return not raw or raw.upper() in ("UTC", "GMT", "Z")


def utc_to_report_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_report_tz())


# Единый «операторский» вид даты/времени в Telegram (без приставки UTC): сначала время.
OPERATOR_DATETIME_FMT = "%H:%M · %d.%m.%Y"


def format_utc_in_report_zone(dt: Optional[datetime], *, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Строка для отчётов: момент UTC → локальная стена отчёта."""
    if dt is None:
        return ""
    loc = utc_to_report_local(dt)
    return loc.strftime(fmt)


def format_operator_datetime(dt: Optional[datetime]) -> str:
    """Дата/время для HTML-отчётов: зона GEMMA_REPORT_TIMEZONE или UTC без суффикса «UTC»."""
    if dt is None:
        return "—"
    base = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if base.microsecond:
        base = base.replace(microsecond=0)
    loc = utc_to_report_local(base)
    return loc.strftime(OPERATOR_DATETIME_FMT)


def format_operator_datetime_from_iso(value: Any) -> str:
    """ISO-строка, datetime или похожее → format_operator_datetime; при ошибке — укороченный сырой текст."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.microsecond:
            dt = dt.replace(microsecond=0)
        return format_operator_datetime(dt)
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
        if dt.microsecond:
            dt = dt.replace(microsecond=0)
        return format_operator_datetime(dt)
    except (ValueError, TypeError, OSError):
        head = raw.split(".", 1)[0].strip()
        if "+" in head:
            head = head.split("+", 1)[0].strip()
        if head.endswith("Z"):
            head = head[:-1].strip()
        if "T" in head:
            return head.replace("T", " ", 1)
        return head[:32]


def format_usage_digest_slot_caption(slot: Any) -> str:
    """
    Слот дайджеста из usage_learning.digest_slot_utc (вид «YYYY-MM-DDTHH») — календарный час UTC.
    Без локального пояса и без «UTC+3» в тексте: подробности о времени — только в диалоге, если пользователь спросит.
    """
    s = str(slot or "").strip()
    if not s:
        return "—"
    if "T" in s and len(s) <= 14 and s.count("-") == 2:
        try:
            date_part, hp = s.split("T", 1)
            h = int(hp)
            if 0 <= h <= 23:
                return f"{date_part} {h:02d}:00 UTC · слот дайджеста"
        except ValueError:
            pass
    return s


def format_health_snapshot_caption(ts_raw: Any) -> str:
    """Краткая метка для отчётов: только дата/время в зоне GEMMA_REPORT_TIMEZONE (без IANA и без UTC±N в строке)."""
    human = format_operator_datetime_from_iso(ts_raw)
    return human if human else "—"


def log_line_timestamp(created: float) -> str:
    """Строка времени для logging.Formatter (asctime): стена часов в зоне отчёта, без суффикса +03:00."""
    tz = get_report_tz()
    dt = datetime.fromtimestamp(created, tz=tz).replace(microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def json_iso_timestamp(created: float) -> str:
    """Время в JSON-логах: та же стена часов, без IANA и без числового смещения в строке."""
    tz = get_report_tz()
    dt = datetime.fromtimestamp(created, tz=tz).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")
