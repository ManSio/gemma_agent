"""
Subject-Context Decay — clears subject context when N consecutive
messages contain no references to the bound object.

If subject_context decay is enabled, tracks how many turns have
passed without a reference; after the threshold (default 5),
clears the subject context to prevent "sticky" binding.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.safety_config import subject_decay_enabled

logger = logging.getLogger(__name__)

SUBJECT_DECAY_VERSION = "1.0.0"

_DECAY_THRESHOLD = 5

_state: Dict[str, Dict[str, Any]] = {}


def _make_key(user_id: str, group_id: Optional[str]) -> str:
    return f"{user_id or 'anon'}:{group_id or ''}"


def _ensure(user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
    key = _make_key(user_id, group_id)
    if key not in _state:
        _state[key] = {
            "turns_without_reference": 0,
            "subject_context": None,
        }
    return _state[key]


def record_turn(
    *,
    user_id: str,
    group_id: Optional[str] = None,
    has_reference: bool = False,
) -> None:
    """Record a turn: increment counter if no reference, reset otherwise."""
    if not subject_decay_enabled():
        return
    st = _ensure(user_id, group_id)
    if has_reference:
        st["turns_without_reference"] = 0
    else:
        st["turns_without_reference"] = st.get("turns_without_reference", 0) + 1


def should_clear(
    *,
    user_id: str,
    group_id: Optional[str] = None,
) -> bool:
    """Check if subject context should be cleared due to decay."""
    if not subject_decay_enabled():
        return False
    st = _ensure(user_id, group_id)
    return st.get("turns_without_reference", 0) >= _DECAY_THRESHOLD


def clear_subject_context(
    *,
    user_id: str,
    group_id: Optional[str] = None,
) -> None:
    """Clear the subject context and reset counter."""
    if not subject_decay_enabled():
        return
    st = _ensure(user_id, group_id)
    st["turns_without_reference"] = 0
    st["subject_context"] = None
    logger.debug("subject_context: cleared for user_id=%s", user_id)


def get_turns_without_reference(
    user_id: str,
    group_id: Optional[str] = None,
) -> int:
    st = _ensure(user_id, group_id)
    return int(st.get("turns_without_reference", 0))
