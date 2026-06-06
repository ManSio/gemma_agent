"""Пост-обработка ответа brain: persona, digest, события инструментов."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from core.brain.env import env_flag
from core.brain.runtime import _persona
from core.brain.text_helpers import strip_chat_markdown_for_telegram
from core.event_bus import bus

logger = logging.getLogger(__name__)


def emit_brain_tool_finished(
    user_id: str,
    context: Dict[str, Any],
    tool_name: str,
    tool_result: Any,
) -> None:
    if not (tool_name or "").strip():
        return
    try:
        err = ""
        ok = True
        if isinstance(tool_result, dict) and tool_result.get("error"):
            ok = False
            err = str(tool_result.get("error") or "")
        gid = None
        if isinstance(context, dict) and context.get("group_id") not in (None, ""):
            gid = str(context.get("group_id")).strip()
        bus.emit_ff(
            "brain.tool_finished",
            {
                "user_id": str(user_id),
                "group_id": gid,
                "tool_name": str(tool_name).strip(),
                "tool_ok": ok,
                "tool_error": err[:800],
            },
        )
    except Exception as e:
        logger.debug("pipeline_postprocess optional failed: %s", e, exc_info=True)


def persona_apply_polished(user_id: str, reply: str, *, user_text: str = "") -> str:
    body = reply or ""
    if env_flag("BRAIN_STRIP_CHAT_MARKDOWN", default=True):
        body = strip_chat_markdown_for_telegram(body)
    try:
        from core.brain.translation_path import is_translation_turn

        if is_translation_turn(user_text):
            return body
    except Exception as e:
        logger.debug("persona_apply_polished: %s", e, exc_info=True)
    return _persona.apply_persona_to_response(user_id, body)


def _brain_standard_recent_count() -> int:
    try:
        return max(4, int((os.getenv("BRAIN_STANDARD_RECENT_COUNT") or "10").strip()))
    except ValueError:
        return 10


def _recent_dialogue_user_turns(recent_dialogue: Any) -> int:
    if not isinstance(recent_dialogue, list):
        return 0
    n = 0
    for row in recent_dialogue:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role in ("user", "human"):
            n += 1
    return n


def session_digest_skip_when_recent_full(recent_dialogue: Any) -> bool:
    """MEM-4: не дублировать session_digest, если recent уже покрывает контекст."""
    raw = (os.getenv("SESSION_DIGEST_SKIP_WHEN_RECENT_FULL") or "true").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    limit = _brain_standard_recent_count()
    return _recent_dialogue_user_turns(recent_dialogue) >= limit


def get_session_digest(user_id: str, group_id: Optional[str]) -> str:
    try:
        from core.session_digest import to_prompt_digest

        return to_prompt_digest(user_id, group_id)
    except Exception as e:
        logger.debug("get_session_digest: %s", e, exc_info=True)
        return ""


def get_session_digest_for_prompt(
    user_id: str,
    group_id: Optional[str],
    *,
    recent_dialogue: Any = None,
) -> str:
    if session_digest_skip_when_recent_full(recent_dialogue):
        return ""
    return get_session_digest(user_id, group_id)
