"""
Структурированный журнал ходов для агентов и Ops API.

Файл: data/runtime/ops_trace.jsonl (GEMMA_OPS_TRACE_PATH).
Каждая строка — полный снимок: вопрос, ответ, recent_messages, хвост архива,
план, reasoning, эвристики сдвига «ответ не на тот вопрос».
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_INSTALLED = False

_GREETING_RE = re.compile(
    r"(?i)^(привет|здравствуй|hello|hi\b|чем могу|как (я могу|дела|ты))",
)
_SHIFT_GREETING_RE = re.compile(r"(?i)привет|чем могу помочь|как (я могу|твои дела)")


def ops_trace_enabled() -> bool:
    raw = os.getenv("OPS_TRACE_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def log_path() -> Path:
    raw = (os.getenv("GEMMA_OPS_TRACE_PATH") or "").strip()
    if raw:
        p = Path(raw)
    else:
        try:
            from core.turn_observer import log_path as _turns_path

            p = _turns_path().parent / "ops_trace.jsonl"
        except Exception:
            root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
            p = Path(root) / "data" / "runtime" / "ops_trace.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip_rows(rows: Any, n: int = 8) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows[-n:]:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "role": str(r.get("role") or ""),
                "text": str(r.get("text") or r.get("content") or "")[:400],
            }
        )
    return out


def _pairing_compare_limit() -> int:
    import os

    try:
        n = int((os.getenv("OPS_TRACE_PAIRING_COMPARE_CHARS") or "400").strip())
    except ValueError:
        n = 400
    return max(80, min(n, 8000))


def _check_storage_pairing(
    recent_after: List[Dict[str, Any]],
    user_text: str,
    assistant_text: str,
) -> Tuple[bool, str]:
    if len(recent_after) < 2:
        return True, ""
    u, a = recent_after[-2], recent_after[-1]
    ur = str(u.get("role") or "").lower()
    ar = str(a.get("role") or "").lower()
    if ur not in ("user", "human") or ar not in ("assistant", "bot", "model"):
        return False, "orphan_roles_in_recent"
    lim = _pairing_compare_limit()
    ut = (user_text or "").strip()[:2000]
    at = (assistant_text or "").strip()[:8000]
    stored_u = (u.get("text") or "").strip()
    stored_a = (a.get("text") or "").strip()
    if stored_u[:lim] != ut[:lim]:
        return False, "recent_user_text_mismatch"
    if stored_a[:lim] != at[:lim]:
        return False, "recent_assistant_text_mismatch"
    return True, ""


def _check_topic_shift(user_text: str, assistant_text: str) -> Tuple[bool, str]:
    """Грубая эвристика: приветствие на содержательный вопрос или явный off-topic."""
    ut = (user_text or "").strip()
    at = (assistant_text or "").strip()
    if not ut or not at:
        return False, ""
    if len(ut) > 12 and _SHIFT_GREETING_RE.search(at[:120]) and not _GREETING_RE.match(ut):
        return True, "greeting_on_substantive_question"
    # Короткий вопрос «почему» без продолжения — отдельный кейс (backfill)
    if ut.lower() in {"почему", "зачем", "как", "что", "покажи", "повтори"} and len(at) > 80:
        return True, "long_answer_on_single_word"
    return False, ""


def reply_topic_mismatch(user_text: str, assistant_text: str) -> bool:
    """Ответ явно не про текущий вопрос (для issues в trace)."""
    ut = (user_text or "").strip()
    at = (assistant_text or "").strip()
    if len(ut) < 8 or len(at) < 20:
        return False
    if _SHIFT_GREETING_RE.search(at[:160]) and not _GREETING_RE.match(ut):
        return True
    overlap = 0.0
    try:
        from core.telegram_output_guard import _overlap_with_user_query

        overlap = _overlap_with_user_query(ut, at)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'ops_trace', e, exc_info=True)
    if overlap < 0.08 and any(
        w in ut.lower() for w in ("почему", "какой", "какая", "зачем", "когда", "где", "что такое")
    ):
        return True
    return False


def analyze_turn(
    *,
    user_text: str,
    assistant_text: str,
    recent_after: List[Dict[str, Any]],
) -> List[str]:
    issues: List[str] = []
    ok, reason = _check_storage_pairing(recent_after, user_text, assistant_text)
    if not ok and reason:
        issues.append(reason)
    shift, sreason = _check_topic_shift(user_text, assistant_text)
    if shift and sreason:
        issues.append(sreason)
    if reply_topic_mismatch(user_text, assistant_text):
        issues.append("reply_topic_mismatch")
    return issues


def append_ops_record(row: Dict[str, Any]) -> None:
    if not ops_trace_enabled():
        return
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except OSError as e:
        logger.debug("ops_trace append: %s", e)


def record_ops_turn(
    *,
    user_id: str,
    group_id: Optional[str],
    channel: str,
    user_text: str,
    assistant_text: str,
    recent_before: Any,
    recent_after: Any,
    archive_tail: Any,
    plan_steps: Optional[List[str]] = None,
    reasoning: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
    latency_ms: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rb = _clip_rows(recent_before)
    ra = _clip_rows(recent_after)
    arch = _clip_rows(archive_tail, 10)
    issues = analyze_turn(user_text=user_text, assistant_text=assistant_text, recent_after=ra)
    row: Dict[str, Any] = {
        "ts": _now_iso(),
        "type": "turn",
        "user_id": str(user_id or ""),
        "group_id": group_id,
        "channel": (channel or "unknown")[:64],
        "trace_id": (trace_id or "")[:64],
        "user_text": (user_text or "")[:500],
        "assistant_text": (assistant_text or "")[:1200],
        "recent_before": rb,
        "recent_after": ra,
        "archive_tail_in_prompt": arch,
        "plan_steps": plan_steps or [],
        "reasoning": reasoning or {},
        "latency_ms": latency_ms,
        "issues": issues,
        "ok": not issues,
    }
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k not in row and v is not None:
                row[k] = v
    append_ops_record(row)
    return row


def read_tail(
    *,
    limit: int = 50,
    user_id: Optional[str] = None,
    since_ts: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = log_path()
    if not path.is_file():
        return []
    limit = max(1, min(500, int(limit)))
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict):
            continue
        if user_id and str(o.get("user_id") or "") != str(user_id):
            continue
        if since_ts and str(o.get("ts") or "") < since_ts:
            continue
        rows.append(o)
        if len(rows) >= limit:
            break
    rows.reverse()
    return rows


def load_user_dialogue_view(user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
    """Снимок behavior_store + архив для отладки (без секретов)."""
    out: Dict[str, Any] = {"user_id": user_id, "group_id": group_id}
    try:
        from core.behavior_store import BehaviorStore
        from core.message_archive import load_message_archive_items
        from core.context_compression import normalize_dialogue_message_rows

        rec = BehaviorStore().load(user_id, group_id)
        rm = rec.get("recent_messages") if isinstance(rec, dict) else []
        out["recent_messages"] = normalize_dialogue_message_rows(rm)[-12:]
        out["dialogue_summary"] = str(rec.get("dialogue_summary") or "")[:800]
        arch = load_message_archive_items(user_id, group_id)
        out["archive_tail"] = normalize_dialogue_message_rows(arch)[-16:]
        out["archive_count"] = len(arch) if isinstance(arch, list) else 0
    except Exception as e:
        out["error"] = str(e)
    return out


def install_ops_trace() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    logger.info("ops_trace: logging to %s", log_path())
