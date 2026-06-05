"""
Hermes-style Layer 1: старые «простыни» tool output в recent_messages → placeholder.

Без LLM. Последние N реплик ассистента с tool-blob остаются целиком.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from core.runtime_telegram_settings import effective_bool

_TOOL_MARKER_RE = re.compile(
    r"(?i)(tool_call|_brain_second_truncated|результат инструмента|available tools)"
)
_JSONISH_START = re.compile(r"^\s*[\{\[]")


def tool_output_trim_enabled() -> bool:
    return effective_bool("CONTEXT_TOOL_OUTPUT_TRIM_ENABLED", default=True)


def _keep_recent_tool_blobs() -> int:
    try:
        return max(0, int((os.getenv("CONTEXT_TOOL_OUTPUT_KEEP_RECENT") or "2").strip()))
    except ValueError:
        return 2


def _placeholder() -> str:
    return (
        (os.getenv("CONTEXT_TOOL_OUTPUT_PLACEHOLDER") or "").strip()
        or "[старый результат инструмента сжат; при необходимости повторите запрос]"
    )


def _min_chars_to_trim() -> int:
    try:
        return max(400, int((os.getenv("CONTEXT_TOOL_OUTPUT_MIN_CHARS") or "1200").strip()))
    except ValueError:
        return 1200


def looks_like_tool_result_blob(text: str) -> bool:
    """Эвристика: ответ ассистента похож на сырой JSON/tool output, а не на реплику пользователю."""
    t = (text or "").strip()
    if not t:
        return False
    if _TOOL_MARKER_RE.search(t):
        return True
    if len(t) < _min_chars_to_trim():
        return False
    if _JSONISH_START.match(t):
        try:
            json.loads(t[:8000])
            return True
        except json.JSONDecodeError:
            if '"ok"' in t[:400] or '"error"' in t[:400]:
                return True
    if len(t) > 2500 and ('"text"' in t or '"preview"' in t) and _JSONISH_START.match(t[:80]):
        return True
    return False


def trim_tool_outputs_in_dialogue(rows: Any, *, keep_recent_full: int | None = None) -> List[Dict[str, Any]]:
    """
    С конца диалога оставляем до `keep_recent_full` последних tool-blob без сжатия;
    более старые blob-ответы ассистента заменяем placeholder.
    """
    if not tool_output_trim_enabled() or not isinstance(rows, list):
        return rows if isinstance(rows, list) else []
    keep = _keep_recent_tool_blobs() if keep_recent_full is None else max(0, int(keep_recent_full))
    ph = _placeholder()
    out: List[Dict[str, Any]] = []
    blobs_seen_from_end = 0
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        nr = dict(row)
        role = str(nr.get("role") or "").lower()
        txt = str(nr.get("text") or "")
        if role in ("assistant", "bot", "model") and looks_like_tool_result_blob(txt):
            blobs_seen_from_end += 1
            if blobs_seen_from_end > keep:
                nr["text"] = ph
                nr["_tool_output_trimmed"] = True
        out.append(nr)
    out.reverse()
    return out
