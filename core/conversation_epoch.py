"""
conversation_epoch — устойчивый идентификатор «темы» диалога в behavior_store.

Связь с KV: bump → reset_dialog_state → kv_session_epoch++ (session_stickiness).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _idle_ttl_sec() -> float:
    try:
        return max(3600.0, float((os.getenv("CONVERSATION_EPOCH_IDLE_TTL_SEC") or "86400").strip()))
    except ValueError:
        return 86400.0


def _epoch_block(rec: Dict[str, Any]) -> Dict[str, Any]:
    raw = rec.get("conversation_epoch")
    if isinstance(raw, dict):
        return dict(raw)
    return {"id": 0, "started_at": "", "last_activity_at": ""}


def get_epoch_id(rec: Dict[str, Any]) -> int:
    return int(_epoch_block(rec).get("id") or 0)


def touch_activity(rec: Dict[str, Any]) -> None:
    blk = _epoch_block(rec)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not blk.get("started_at"):
        blk["started_at"] = now
    blk["last_activity_at"] = now
    rec["conversation_epoch"] = blk


def _parse_iso_ts(s: str) -> float:
    raw = (s or "").strip()
    if not raw:
        return 0.0
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def maybe_idle_bump_epoch(
    rec: Dict[str, Any],
    *,
    user_id: str,
    group_id: Optional[str],
) -> Optional[str]:
    """Если простой > TTL — новый epoch (без очистки recent — только KV/диалог-state)."""
    blk = _epoch_block(rec)
    last = _parse_iso_ts(str(blk.get("last_activity_at") or ""))
    if last <= 0:
        return None
    if (time.time() - last) < _idle_ttl_sec():
        return None
    bump_conversation_epoch(rec, user_id=user_id, group_id=group_id, reason="idle_ttl")
    return "idle_ttl"


def bump_conversation_epoch(
    rec: Dict[str, Any],
    *,
    user_id: str,
    group_id: Optional[str],
    reason: str,
    clear_dialogue: bool = False,
) -> int:
    blk = _epoch_block(rec)
    new_id = int(blk.get("id") or 0) + 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec["conversation_epoch"] = {
        "id": new_id,
        "started_at": now,
        "last_activity_at": now,
        "last_bump_reason": (reason or "")[:120],
    }
    if clear_dialogue:
        rec["recent_messages"] = []
        rec["dialogue_summary"] = ""
        rec["topic_tracking"] = {"current": "", "snippet": ""}
        rec["session_first_user_text"] = ""
        ds = dict(rec.get("dialogue_state") or {})
        ds["turn_index"] = 0
        rec["dialogue_state"] = ds
        # /new (clear_dialogue=True) должен также сбрасывать активный dialogue_slot,
        # иначе следующий ход может уйти в follow-up логику (например, article_thread).
        try:
            from core.dialogue_slots import clear_slot

            clear_slot(rec)
        except Exception as e:
            logger.debug("conversation_epoch clear_slot: %s", e)
        rp = dict(rec.get("routing_prefs") or {})
        if rp.pop("pending_correction", None) is not None:
            rec["routing_prefs"] = rp
    try:
        from core.dialog_state import reset_dialog_state

        reset_dialog_state(reason or "conversation_epoch", user_id=str(user_id), group_id=group_id)
    except Exception as e:
        logger.debug("conversation_epoch reset_dialog_state: %s", e)
    try:
        from core.brain.session_stickiness import force_session_reset

        force_session_reset(user_id=user_id, group_id=group_id, reason=reason or "conversation_epoch")
    except Exception as e:
        logger.debug("conversation_epoch force_session_reset: %s", e)
    logger.info(
        "conversation_epoch bump id=%s reason=%s user=%s",
        new_id,
        reason,
        user_id,
        extra={"gemma_event": "conversation_epoch_bump", "epoch_id": new_id},
    )
    return new_id


def start_new_conversation(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    *,
    reason: str = "user_new",
) -> Tuple[int, Dict[str, Any]]:
    rec = behavior_store.load(user_id, group_id)
    new_id = bump_conversation_epoch(
        rec, user_id=user_id, group_id=group_id, reason=reason, clear_dialogue=True
    )
    behavior_store.save(user_id, group_id, rec)
    return new_id, rec
