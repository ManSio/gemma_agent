"""
URL из текущего сообщения и недавней нити (диалог + групповая лента).

Нужно, чтобы запросы вроде «скачай документацию» после сообщения со ссылкой
не теряли URL — модель не всегда выдаёт TOOL_CALL.
"""
from __future__ import annotations

import re
from typing import Any, List, Sequence, Union

_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
_FETCH_SIGNAL = re.compile(
    r"(?i)(скачай|скачать|загрузи|загрузить|документ|документац|прочитай|прочесть|"
    r"содержим|содержание|страниц|страницу|html|urlfetch|fetch|спарс|парс|извлек|extract|"
    r"что на сайте|по ссылке|открой ссылку|что там|what\s+is\s+on|download|scrape|curl|"
    r"можешь\s+скач|можешь\s+загруз|дай\s+содерж|покажи\s+содерж|текст\s+страниц)",
)


def _normalize_url(u: str) -> str:
    return u.rstrip(").,;]\"'")


def _text_from_dialogue_row(row: Union[dict, Any]) -> str:
    if isinstance(row, dict):
        return str(row.get("text") or row.get("content") or "")
    return str(row or "")


def gather_urls_chronological_for_brain(
    user_text: str,
    recent_dialogue: Sequence[Any],
    transcript_compact: str = "",
) -> List[str]:
    """
    Порядок: старые реплики из recent_dialogue, затем текущее user_text, затем сжатая лента группы.
    Последний элемент списка — последняя появившаяся уникальная ссылка (часто то, что имели в виду).
    """
    parts: List[str] = []
    if isinstance(recent_dialogue, list):
        for row in recent_dialogue[-32:]:
            parts.append(_text_from_dialogue_row(row))
    parts.append(user_text or "")
    parts.append(transcript_compact or "")

    ordered: List[str] = []
    seen_lower = set()
    for ch in parts:
        for raw in _URL_RE.findall(ch or ""):
            u = _normalize_url(raw)
            key = u.lower()
            if key not in seen_lower:
                seen_lower.add(key)
                ordered.append(u)
    return ordered


def user_signals_url_content_fetch(user_text: str, urls: List[str]) -> bool:
    """Нужно ли без участия LLM дернуть UrlFetch."""
    if not urls:
        return False
    ut = (user_text or "").strip()
    if not ut:
        return True
    if _FETCH_SIGNAL.search(ut):
        return True
    stripped = ut
    for u in urls:
        stripped = stripped.replace(u, "").strip()
    if len(stripped) < 12:
        return True
    return False
