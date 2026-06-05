"""Сжатие контекста диалога: обрезка сообщений и сводок с protect_last_n."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int((os.getenv(name) or "").strip() or str(default)))
    except (TypeError, ValueError):
        return default


def context_compression_enabled() -> bool:
    return _truthy("CONTEXT_COMPRESSION_ENABLED", True)


def _protect_last_n() -> int:
    """
    Сколько ПОСЛЕДНИХ сообщений НЕ сжимать (protect_last_n).
    По умолчанию 2: текущий ход (user+assistant) остаётся полным.
    """
    return _i("CONTEXT_PROTECT_LAST_N", 2, minimum=0)


def normalize_dialogue_message_rows(rows: Any) -> List[Dict[str, Any]]:
    """Drop empty rows; remove orphan assistant at head and orphan user at tail."""
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not str(row.get("text") or "").strip():
            continue
        out.append(row)
    while out and str(out[0].get("role") or "").lower() in ("assistant", "bot", "model"):
        out.pop(0)
    # Только «висящий» ход user без ответа ассистента (user сразу после assistant).
    # Не снимать цепочку user,user — иначе сжатие и prompt recent обнуляются.
    if len(out) >= 2:
        last_role = str(out[-1].get("role") or "").lower()
        prev_role = str(out[-2].get("role") or "").lower()
        if last_role in ("user", "human") and prev_role in ("assistant", "bot", "model"):
            out.pop()
    return out


def trim_dialogue_messages_paired(rows: Any, max_messages: int) -> List[Dict[str, Any]]:
    """
    FIFO trim by message count but keep user/assistant pairs aligned.
    Odd max_messages is rounded down to even so the slice never starts with a lone assistant.
    """
    msgs = normalize_dialogue_message_rows(rows)
    if not msgs:
        return []
    cap = max(2, int(max_messages))
    if cap % 2:
        cap -= 1
    if len(msgs) <= cap:
        return msgs
    return normalize_dialogue_message_rows(msgs[-cap:])


def compress_recent_dialogue(rows: Any, *, protect_last_n: int | None = None) -> List[Dict[str, Any]]:
    """
    FIFO: keep last N messages, clip older ones.
    The last `protect_last_n` messages are NOT clipped — they remain verbatim.

    protect_last_n defaults to CONTEXT_PROTECT_LAST_N env.
    """
    if not isinstance(rows, list):
        return []
    if not context_compression_enabled():
        return normalize_dialogue_message_rows(rows)
    rows = normalize_dialogue_message_rows(rows)

    if protect_last_n is None:
        protect_last_n = _protect_last_n()

    keep_last = _i("CONTEXT_RECENT_KEEP_MESSAGES", 5, minimum=4)
    assistant_clip = _i("CONTEXT_ASSISTANT_CLIP_CHARS", 700, minimum=120)
    user_clip = _i("CONTEXT_USER_CLIP_CHARS", 420, minimum=80)

    # Берём keep_last последних
    candidate_slice = rows[-keep_last:] if len(rows) > keep_last else rows[:]

    # Первые (старые) из этого слайса — сжимаем.
    # Последние protect_last_n — не трогаем.
    compress_end = max(0, len(candidate_slice) - max(0, protect_last_n))

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(candidate_slice):
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "")
        txt = str(row.get("text") or "")
        if not txt.strip():
            continue

        if idx < compress_end:
            # Это сообщение можно сжать
            clip = assistant_clip if role == "assistant" else user_clip
            clean = re.sub(r"\s+", " ", txt).strip()
            if len(clean) > clip:
                clean = clean[: clip - 1] + "…"
            nr = dict(row)
            nr["text"] = clean
            out.append(nr)
        else:
            # protected: полный текст, без изменений
            out.append(dict(row))

    return normalize_dialogue_message_rows(out)


def compress_dialogue_summary(summary: Any) -> str:
    s = str(summary or "").strip()
    if not s:
        return ""
    if not context_compression_enabled():
        return s
    max_chars = _i("CONTEXT_SUMMARY_MAX_CHARS", 1200, minimum=200)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    out = s
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out
