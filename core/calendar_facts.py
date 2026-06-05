"""
Жёсткие календарные факты для LLM: ISO и день недели по шаблону ДД.ММ.ГГГГ / ММ.ДД.ГГГГ.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

_DATE_DOT_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def _ru_weekday(d: date) -> str:
    return _WEEKDAYS_RU[d.weekday()]


def _try_dd_mm_yyyy(day: int, month: int, year: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _try_mm_dd_yyyy(month: int, day: int, year: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _wants_calendar_fact(text: str) -> bool:
    if not text or _DATE_DOT_RE.search(text) is None:
        return False
    t = text.lower()
    keys = (
        "iso",
        "день недели",
        "день недел",
        "какой день",
        "какая дата",
        "неделю",
        "календар",
        "дд.мм",
        "мм.дд",
        "трактовк",
        "gregorian",
    )
    return any(k in t for k in keys)


def _describe_pair(label_dm: str, d_dm: date, label_md: str, d_md: date) -> Tuple[str, ...]:
    if d_dm == d_md:
        return (
            f"- Запись однозначна как {label_dm}: ISO {d_dm.isoformat()}, день недели — {_ru_weekday(d_dm)}.",
        )
    return (
        f"- Если {label_dm}: ISO {d_dm.isoformat()}, день недели — {_ru_weekday(d_dm)}.",
        f"- Если {label_md}: ISO {d_md.isoformat()}, день недели — {_ru_weekday(d_md)}.",
    )


def build_calendar_date_hint_for_llm(user_text: str) -> str:
    """
    Возвращает текст для external_hint или пустую строку.
    Первое совпадение DD.MM.YYYY в сообщении.
    """
    if not _wants_calendar_fact(user_text):
        return ""
    m = _DATE_DOT_RE.search(user_text)
    if not m:
        return ""
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    token = m.group(0)
    d_dm = _try_dd_mm_yyyy(a, b, y)
    d_md = _try_mm_dd_yyyy(a, b, y)
    lines = [
        "Календарь (вычислено кодом datetime; используй эти ISO и дни недели, не выдумывай):",
    ]
    if d_dm and d_md:
        lines.extend(_describe_pair("ДД.ММ.ГГГГ (естественно для РФ/Европы)", d_dm, "ММ.ДД.ГГГГ (US)", d_md))
    elif d_dm:
        lines.append(
            f"- Как ДД.ММ.ГГГГ: ISO {d_dm.isoformat()}, день недели — {_ru_weekday(d_dm)} "
            f"(как ММ.ДД.ГГГГ дата не существует).",
        )
    elif d_md:
        lines.append(
            f"- Как ММ.ДД.ГГГГ: ISO {d_md.isoformat()}, день недели — {_ru_weekday(d_md)} "
            f"(как ДД.ММ.ГГГГ дата не существует).",
        )
    else:
        return ""
    lines.append(f"Исходная строка в сообщении пользователя: {token}.")
    lines.append("В ответе явно скажи, какую трактовку формата ты используешь; при двух вариантах — оба.")
    return "\n".join(lines)
