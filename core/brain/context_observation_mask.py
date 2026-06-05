"""
Observation masking для brain prompt (context rot mitigation).

Старые объёмные ответы ассистента (tool output, JSON) сжимаются в плейсхолдер;
последние N реплик ассистента и весь user — без изменений.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

_TOOL_OBSERVATION_RE = re.compile(
    r"(?i)(\"ok\"\s*:\s*(true|1)|\"http_status\"|\"truncated\"\s*:|"
    r"UniversalSearch|UrlFetch\.|TOOL_CALL|last_tool_result|"
    r"Currency API data|Open-Meteo|searxng)"
)


def observation_mask_enabled() -> bool:
    raw = (os.getenv("BRAIN_CONTEXT_OBSERVATION_MASK_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _keep_recent_assistant() -> int:
    try:
        v = int((os.getenv("BRAIN_CONTEXT_MASK_KEEP_RECENT_ASSISTANT") or "2").strip())
    except ValueError:
        v = 2
    return max(0, min(v, 8))


def _max_assistant_chars() -> int:
    try:
        v = int((os.getenv("BRAIN_CONTEXT_MASK_ASSISTANT_CHARS") or "2400").strip())
    except ValueError:
        v = 2400
    return max(400, min(v, 12000))


def _external_hint_max_chars() -> int:
    try:
        v = int((os.getenv("BRAIN_CONTEXT_EXTERNAL_HINT_MAX_CHARS") or "7200").strip())
    except ValueError:
        v = 7200
    return max(1200, min(v, 24000))


def _is_tool_heavy_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if len(t) > _max_assistant_chars():
        return True
    return bool(_TOOL_OBSERVATION_RE.search(t))


def _placeholder(orig_len: int) -> str:
    return (
        f"[ранний ответ сокращён для контекста, было ~{orig_len} симв.; "
        f"суть в последних репликах и external_hint]"
    )


def mask_observation_dialogue(rows: Any) -> List[Dict[str, Any]]:
    """Маскирует старые tool-heavy реплики assistant в recent_dialogue."""
    if not observation_mask_enabled():
        return list(rows) if isinstance(rows, list) else []
    if not isinstance(rows, list) or not rows:
        return []

    keep = _keep_recent_assistant()
    max_chars = _max_assistant_chars()
    assistant_from_end = 0
    out_rev: List[Dict[str, Any]] = []

    for row in reversed(rows):
        if not isinstance(row, dict):
            out_rev.append(row)  # type: ignore[arg-type]
            continue
        role = str(row.get("role") or "").strip().lower()
        text = str(row.get("text") or row.get("content") or "").strip()
        new_row = dict(row)
        if role == "assistant" and text:
            assistant_from_end += 1
            heavy = _is_tool_heavy_text(text)
            if assistant_from_end > keep and (heavy or len(text) > max_chars):
                ph = _placeholder(len(text))
                new_row["content"] = ph
                new_row["text"] = ph
                new_row["_observation_masked"] = True
        out_rev.append(new_row)

    return list(reversed(out_rev))


def mask_external_hint(hint: str) -> str:
    """Сжимает середину длинного external_hint (часы/погода в начале сохраняются)."""
    if not observation_mask_enabled():
        return hint or ""
    raw = (hint or "").strip()
    if not raw:
        return ""
    cap = _external_hint_max_chars()
    if len(raw) <= cap:
        return raw

    blocks = [b.strip() for b in re.split(r"\n\n+", raw) if b.strip()]
    if len(blocks) <= 3:
        return raw[: cap - 20] + "\n…[external_hint усечён]"

    head = blocks[0]
    tail = blocks[-2:]
    mid = blocks[1:-2]
    mid_short = []
    for b in mid:
        if len(b) > 320:
            mid_short.append(b[:280] + "…")
        else:
            mid_short.append(b)
    merged = "\n\n".join([head, *mid_short, *tail])
    if len(merged) <= cap:
        return merged
    return merged[: cap - 24] + "\n…[external_hint усечён]"
