"""Мягкая обрезка контекста диалога (без сброса user_facts)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _keep_recent_default() -> int:
    try:
        return max(2, min(24, int((os.getenv("SESSION_TRIM_KEEP_RECENT") or "6").strip())))
    except ValueError:
        return 6


def trim_user_session(
    user_id: str,
    group_id: Optional[str] = None,
    *,
    keep_recent: Optional[int] = None,
    clear_summary: bool = True,
    clear_dialogue_slots: bool = False,
    bump_kv: bool = False,
) -> Dict[str, Any]:
    """
    Урезать recent_messages и опционально summary / слоты / KV epoch.
    Не трогает user_facts и message_archive.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return {"ok": False, "error": "empty user_id"}
    keep = keep_recent if keep_recent is not None else _keep_recent_default()
    keep = max(2, min(24, int(keep)))

    report: Dict[str, Any] = {
        "ok": True,
        "user_id": uid,
        "group_id": group_id,
        "keep_recent": keep,
        "steps": [],
    }

    try:
        from core.behavior_store import BehaviorStore

        bs = BehaviorStore()
        rec = bs.load(uid, group_id)
    except Exception as e:
        logger.debug("session_trim load: %s", e)
        return {"ok": False, "error": str(e)}

    msgs = rec.get("recent_messages")
    if not isinstance(msgs, list):
        msgs = []
    before = len(msgs)
    if before > keep:
        rec["recent_messages"] = msgs[-keep:]
        report["steps"].append(f"recent_messages {before} -> {keep}")
    else:
        report["steps"].append(f"recent_messages kept ({before} <= {keep})")

    if clear_summary:
        prev_len = len(str(rec.get("dialogue_summary") or ""))
        rec["dialogue_summary"] = ""
        if prev_len:
            report["steps"].append(f"dialogue_summary cleared ({prev_len} chars)")

    rec["topic_tracking"] = {"current": "", "snippet": ""}

    if clear_dialogue_slots:
        rp = dict(rec.get("routing_prefs") or {})
        if rp.pop("dialogue_slot", None) is not None:
            rec["routing_prefs"] = rp
            report["steps"].append("dialogue_slot cleared")

    try:
        bs.save(uid, group_id, rec)
        report["steps"].append("behavior saved")
    except Exception as e:
        logger.debug("session_trim save: %s", e)
        return {"ok": False, "error": str(e)}

    if bump_kv:
        try:
            from core.brain.session_stickiness import force_session_reset

            sid = force_session_reset(
                user_id=uid,
                group_id=group_id,
                reason="session_trim",
            )
            report["steps"].append(f"kv_session reset -> {sid}")
        except Exception as e:
            logger.debug("session_trim kv: %s", e)
            report["steps"].append(f"kv_session reset failed: {e}")

    try:
        from core.dialog_state import reset_dialog_state

        reset_dialog_state("session_trim", user_id=uid, group_id=group_id)
        report["steps"].append("dialog_state reset")
    except Exception as e:
        logger.debug("session_trim dialog_state: %s", e)

    return report


def format_trim_report_html(report: Dict[str, Any]) -> str:
    if not report.get("ok"):
        err = str(report.get("error") or "ошибка")
        return f"<b>session_trim</b>: {err}"
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    lines = [
        "<b>Контекст обрезан</b>",
        f"user_id: <code>{report.get('user_id')}</code>",
        f"оставлено реплик: {report.get('keep_recent')}",
    ]
    if steps:
        lines.extend(str(s) for s in steps)
    else:
        lines.append("без изменений")
    return "\n".join(lines)
