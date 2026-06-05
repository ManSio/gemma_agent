"""Валидация TOOL_CALL и переупорядочивание списка инструментов в промпте."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from core.brain.text_helpers import parse_tool_call, tool_call_marker_body_incomplete


def tool_call_validation_error(tool_call: Dict[str, Any], allowed_names: Set[str]) -> str:
    if not tool_call:
        return ""
    name = tool_call.get("name")
    if not isinstance(name, str) or not name.strip():
        return "поле name пустое или не строка"
    name = name.strip()
    if name not in allowed_names:
        return f"инструмент {name!r} не из текущего списка; допустимы только перечисленные имена"
    args = tool_call.get("args")
    if args is not None and not isinstance(args, dict):
        return "поле args должно быть JSON-объектом"
    return ""


def describe_tool_call_retry_issue(first_content: str, allowed_tool_names: Set[str]) -> str:
    """
    Пустая строка — retry не нужен (нет маркера TOOL_CALL или вызов валиден).
    Иначе короткая причина для [исправление] в промпте.
    """
    text = first_content or ""
    if "TOOL_CALL:" not in text:
        return ""
    tc = parse_tool_call(text)
    if tc:
        return tool_call_validation_error(tc, allowed_tool_names)
    if tool_call_marker_body_incomplete(text):
        return (
            "JSON после TOOL_CALL обрезан или с ошибкой. Повтори одним компактным JSON: "
            '{"name":"UrlFetch.fetch_page","args":{"url":"https://полная_ссылка"}} — без переносов внутри url.'
        )
    return "TOOL_CALL не разобран — один JSON-объект с полями name и args (строки в двойных кавычках)."


def prioritize_tools_by_hint(tools_info: Dict[str, str], suggested: List[str]) -> Dict[str, str]:
    if not suggested or not tools_info:
        return dict(tools_info)
    out: Dict[str, str] = {}
    for s in suggested:
        if s in tools_info:
            out[s] = tools_info[s]
    for k, v in tools_info.items():
        if k not in out:
            out[k] = v
    return out


def text_before_tool_call(text: str) -> str:
    t = (text or "").strip()
    if "TOOL_CALL:" in t:
        t = (t.split("TOOL_CALL:", 1)[0] or "").strip()
    if re.search(r"<\s*tool_call\b", t, re.IGNORECASE):
        t = re.split(r"<\s*tool_call\b", t, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return t
