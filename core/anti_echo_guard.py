"""Template-aware anti-echo: не слать погоду/шаблон на чужой вопрос."""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

_INTENTIONAL_REPEAT_RE = re.compile(
    r"(?i)\b(повтори|ещё\s*раз|еще\s*раз|снова|то\s*же\s*самое|дублируй)\b"
)
_WEATHER_REPLY_RE = re.compile(
    r"(?i)(wttr\.in|open-meteo|open\s*meteo|погода\s*\(|🌡|°c|°f|"
    r"ветер\s*:|влажност|осадк|прогноз\s+на\s+\d)"
)
_IDENTITY_USER_RE = re.compile(
    r"(?i)(как\s+(меня|моё|мое)\s+зовут|моё\s+имя|мое\s+имя|кто\s+я\b|как\s+меня\s+звать)"
)
_DAY_USER_RE = re.compile(
    r"(?i)(какой\s+сегодня\s+день|какое\s+сегодня\s+число|какая\s+сегодня\s+дата|"
    r"какой\s+день\s+недели|какое\s+число\s+сегодня)"
)
_HOLIDAY_USER_RE = re.compile(
    r"(?i)(какой\s+праздник|что\s+празднуют|какой\s+сегодня\s+праздник)"
)


def anti_echo_guard_enabled() -> bool:
    """Включён ли template anti-echo в pre_send."""
    raw = os.getenv("ANTI_ECHO_GUARD_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def user_question_bucket(user_text: str) -> str:
    """Грубый bucket вопроса пользователя для anti-echo."""
    ut = (user_text or "").strip()
    if not ut:
        return "empty"
    if _IDENTITY_USER_RE.search(ut):
        return "identity"
    if _DAY_USER_RE.search(ut):
        return "day"
    if _HOLIDAY_USER_RE.search(ut):
        return "holiday"
    if re.search(r"(?i)\bпогод", ut):
        return "weather"
    return "other"


def looks_like_weather_template(reply: str) -> bool:
    """Ответ похож на сводку погоды (wttr/Open-Meteo шаблон)."""
    rep = (reply or "").strip()
    if len(rep) < 24:
        return False
    return bool(_WEATHER_REPLY_RE.search(rep))


def detect_template_echo_issues(
    user_text: str,
    reply: str,
    last_assistant: str = "",
) -> List[str]:
    """Вернуть issue-теги если reply — чужой шаблон (Jun13-class)."""
    if not anti_echo_guard_enabled():
        return []
    ut = (user_text or "").strip()
    rep = (reply or "").strip()
    if not ut or not rep:
        return []
    if _INTENTIONAL_REPEAT_RE.search(ut):
        return []
    bucket = user_question_bucket(ut)
    if bucket in ("weather", "empty", "other"):
        return []
    if not looks_like_weather_template(rep):
        return []
    issues: List[str] = []
    issues.append("template_echo_weather")
    MONITOR.inc("anti_echo_guard_total")
    return issues


def recover_template_echo_reply(user_text: str, issues: List[str]) -> str:
    """Короткий честный fallback при template echo."""
    bucket = user_question_bucket(user_text)
    if "template_echo_weather" not in issues:
        return ""
    if bucket == "identity":
        return (
            "Похоже, в ответ попала прошлая сводка погоды — это ошибка контекста. "
            "Спросите снова: «как меня зовут?» — отвечу по вашим сохранённым данным."
        )
    if bucket == "day":
        return (
            "Похоже, подставилась старая погода вместо ответа про дату. "
            "Повторите «какой сегодня день?» — отвечу по календарю."
        )
    if bucket == "holiday":
        return (
            "Похоже, подставилась погода вместо праздника. "
            "Повторите вопрос про праздник — отвечу по дате."
        )
    return (
        "Похоже, повторился шаблон погоды не к теме. "
        "Задайте вопрос ещё раз короче."
    )
