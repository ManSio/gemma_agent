"""
Dialog State — unified context reset layer.
Provides reset_dialog_state(reason) to clear subject-context,
reasoning-state, memory-recall, active document, KV-session,
and semantic intent cache.

Triggers:
  - 15+ messages without a task (noise sequence)
  - new topic (semantic intent shift > threshold)
  - collapse-overflow
  - runaway-reasoning
  - tool-call failure
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from core.safety_config import (
    context_reset_enabled,
    noise_sequence_limit,
)

logger = logging.getLogger(__name__)

DIALOG_STATE_VERSION = "1.0.0"

_state: Dict[str, Dict[str, Any]] = {}


def _make_key(user_id: str, group_id: Optional[str]) -> str:
    return f"{user_id or 'anon'}:{group_id or ''}"


def ensure_state(user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
    key = _make_key(user_id, group_id)
    if key not in _state:
        _state[key] = {
            "noise_count": 0,
            "last_topic": "",
            "last_task_ts": time.time(),
            "subject_context": None,
            "reasoning_state": None,
            "memory_recall_allowed": True,
            "active_document": None,
            "semantic_intent_cache": {},
            "kv_session_epoch": 0,
            "runaway_count": 0,
            "tool_fail_count": 0,
        }
    return _state[key]


def reset_dialog_state(
    reason: str,
    *,
    user_id: str,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reset the dialog state for a given user/group.

    Clears: subject-context, reasoning-state, memory-recall,
    active document, KV-session epoch bump, semantic intent cache.

    Returns the previous state for logging.
    """
    key = _make_key(user_id, group_id)
    prev = dict(_state.get(key) or {})
    _state[key] = {
        "noise_count": 0,
        "last_topic": "",
        "last_task_ts": time.time(),
        "subject_context": None,
        "reasoning_state": None,
        "memory_recall_allowed": True,
        "active_document": None,
        "semantic_intent_cache": {},
        "kv_session_epoch": (prev.get("kv_session_epoch", 0) + 1) if prev else 1,
        "runaway_count": 0,
        "tool_fail_count": 0,
    }
    logger.info("dialog_state: reset reason=%s user_id=%s", reason, user_id)
    return prev


def should_trigger_reset(
    *,
    user_id: str,
    group_id: Optional[str] = None,
    has_task: bool = False,
    topic: str = "",
    collapse_overflow: bool = False,
    runaway_reasoning: bool = False,
    tool_call_failure: bool = False,
) -> Optional[str]:
    """Check if any reset trigger condition is met.

    Returns the trigger reason string, or None.
    """
    if not context_reset_enabled():
        return None

    st = ensure_state(user_id, group_id)
    limit = noise_sequence_limit()

    # Noise sequence: N+ messages without a task
    if not has_task:
        st["noise_count"] = st.get("noise_count", 0) + 1
    else:
        st["noise_count"] = 0
        st["last_task_ts"] = time.time()

    if st.get("noise_count", 0) >= limit:
        return f"noise_sequence_{st['noise_count']}"

    # New topic: semantic intent shift
    cur_topic = (topic or "").strip().lower()
    last_topic = str(st.get("last_topic") or "").strip().lower()
    if cur_topic and last_topic and cur_topic != last_topic:
        st["last_topic"] = cur_topic
        return "topic_change"

    if cur_topic:
        st["last_topic"] = cur_topic

    # Collapse overflow
    if collapse_overflow:
        return "collapse_overflow"

    # Runaway reasoning
    if runaway_reasoning:
        st["runaway_count"] = st.get("runaway_count", 0) + 1
        if st["runaway_count"] >= 2:
            return "runaway_reasoning"

    # Tool call failure
    if tool_call_failure:
        st["tool_fail_count"] = st.get("tool_fail_count", 0) + 1
        if st["tool_fail_count"] >= 3:
            return "tool_call_failure"
    else:
        st["tool_fail_count"] = 0

    return None


def get_kv_session_epoch(
    user_id: str,
    group_id: Optional[str] = None,
) -> int:
    st = ensure_state(user_id, group_id)
    return int(st.get("kv_session_epoch", 0))


def is_memory_recall_allowed(
    user_id: str,
    group_id: Optional[str] = None,
) -> bool:
    st = ensure_state(user_id, group_id)
    return bool(st.get("memory_recall_allowed", True))


def get_subject_context(
    user_id: str,
    group_id: Optional[str] = None,
) -> Optional[Any]:
    st = ensure_state(user_id, group_id)
    return st.get("subject_context")


def set_subject_context(
    value: Any,
    *,
    user_id: str,
    group_id: Optional[str] = None,
) -> None:
    st = ensure_state(user_id, group_id)
    st["subject_context"] = value


def get_active_document(
    user_id: str,
    group_id: Optional[str] = None,
) -> Optional[Any]:
    st = ensure_state(user_id, group_id)
    return st.get("active_document")


def set_active_document(
    value: Any,
    *,
    user_id: str,
    group_id: Optional[str] = None,
) -> None:
    st = ensure_state(user_id, group_id)
    st["active_document"] = value
