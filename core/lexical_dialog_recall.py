"""
Лексический поиск по message_archive (дополнение к recent_messages, не замена Mem0).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", re.UNICODE)


def lexical_recall_enabled() -> bool:
    raw = (os.getenv("LEXICAL_DIALOG_RECALL_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _max_chars() -> int:
    try:
        return max(200, min(int((os.getenv("LEXICAL_DIALOG_RECALL_MAX_CHARS") or "1200").strip()), 4000))
    except ValueError:
        return 1200


def _max_snippets() -> int:
    try:
        return max(1, min(int((os.getenv("LEXICAL_DIALOG_RECALL_MAX_SNIPPETS") or "4").strip()), 12))
    except ValueError:
        return 4


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "")}


def build_lexical_recall_hint(
    user_id: str,
    group_id: Optional[str],
    user_text: str,
    *,
    recent_dialogue: Optional[List[Any]] = None,
) -> str:
    if not lexical_recall_enabled() or not (user_text or "").strip():
        return ""
    q_tok = _tokens(user_text)
    if len(q_tok) < 2:
        return ""
    try:
        from core.message_archive import load_message_archive_items
    except Exception as e:
        logger.debug("lexical_recall import: %s", e)
        return ""

    items = load_message_archive_items(str(user_id), group_id)
    if not items:
        return ""

    skip_tail = 6
    pool = items[:-skip_tail] if len(items) > skip_tail else []
    if not pool:
        return ""

    recent_text = ""
    if isinstance(recent_dialogue, list):
        recent_text = " ".join(
            str((m.get("text") if isinstance(m, dict) else m) or "") for m in recent_dialogue[-8:]
        )

    scored: List[tuple[int, str, str]] = []
    for m in pool:
        if not isinstance(m, dict):
            continue
        body = str(m.get("text") or "").strip()
        if not body or len(body) < 12:
            continue
        if body in recent_text:
            continue
        t_tok = _tokens(body)
        if not t_tok:
            continue
        overlap = len(q_tok & t_tok)
        if overlap < 2:
            continue
        role = str(m.get("role") or "?")[:12]
        scored.append((overlap, role, body[:400]))

    if not scored:
        return ""

    scored.sort(key=lambda x: (-x[0], -len(x[2])))
    lines: List[str] = []
    used = 0
    cap = _max_chars()
    for _sc, role, snippet in scored[: _max_snippets()]:
        line = f"- [{role}] {snippet}"
        if used + len(line) > cap:
            break
        lines.append(line)
        used += len(line) + 1

    if not lines:
        return ""
    return "Релевантные фрагменты из прошлого диалога (архив):\n" + "\n".join(lines)
