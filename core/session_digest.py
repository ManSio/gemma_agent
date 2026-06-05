"""
Периодический авто-дайджест ходов (консолидация «сессии») в JSONL без slash-команд.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, DefaultDict, Dict, List

logger = logging.getLogger(__name__)

_buffers: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)


DIGEST_VERSION = "2.0.0"

_DIGEST_MAX_CHARS = 300


def normalize_digest(raw: str) -> str:
    """Normalize digest: strip, deduplicate whitespace, truncate to 300 chars."""
    text = " ".join(str(raw or "").strip().split())
    return text[:_DIGEST_MAX_CHARS]


def to_prompt_digest(user_id: str, group_id: Optional[str] = None) -> str:
    """Build stable session digest (≤ 300 chars) for the prompt.
    Digest is deterministic — no timestamps, no session_id, no rolling counters."""
    if not digest_enabled():
        return ""
    uid = (user_id or "").strip() or "anon"
    buf = _buffers.get(uid, [])
    if not buf:
        return ""
    ok = sum(1 for x in buf if x.get("outcome") == "ok")
    fb = sum(1 for x in buf if x.get("outcome") == "fallback")
    total = len(buf)
    lines = [f"turns={total} ok={ok} fallback={fb}"]
    for s in buf[-5:]:
        lines.append(s.get("outcome", "?") + ":" + s.get("user_excerpt", "")[:40])
    return normalize_digest("; ".join(lines))


def reset_session_digest_buffers() -> None:
    """Для тестов: очистить накопленные буферы."""
    _buffers.clear()


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def digest_enabled() -> bool:
    return _truthy("SESSION_DIGEST_ENABLED", True)


def default_path() -> str:
    p = (os.getenv("GEMMA_SESSION_DIGEST_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "session_digest.jsonl")


def record_turn(
    *,
    user_id: str,
    user_text: str,
    outcome: str,
    intent: str,
    module: str,
) -> None:
    if not digest_enabled():
        return
    uid = (user_id or "").strip() or "anon"
    text = (user_text or "").strip()
    if not text:
        return
    min_chars = int((os.getenv("SESSION_DIGEST_MIN_USER_CHARS") or "2").strip() or "2")
    if len(text) < min_chars:
        return
    sample = {
        "outcome": outcome,
        "intent": intent,
        "module": module,
        "user_excerpt": text[:160],
    }
    _buffers[uid].append(sample)
    every = max(3, int((os.getenv("SESSION_DIGEST_EVERY_N_TURNS") or "12").strip() or "12"))
    max_buf = max(2000, int((os.getenv("SESSION_DIGEST_BUFFER_CHARS") or "6000").strip() or "6000"))
    buf = _buffers[uid]
    buf_chars = sum(len(json.dumps(x, ensure_ascii=False)) for x in buf)
    if len(buf) >= every or buf_chars >= max_buf:
        if _should_skip_digest_flush(buf):
            buf.clear()
            return
        _flush_user(uid, buf)


def _digest_dedup_min_turns() -> int:
    try:
        return max(4, int((os.getenv("SESSION_DIGEST_DEDUP_MIN_TURNS") or "10").strip()))
    except ValueError:
        return 10


def _should_skip_digest_flush(buf: List[Dict[str, Any]]) -> bool:
    """
    C5: если в буфере много ходов и тема одна (по excerpt) — не писать digest в JSONL.
    """
    if not _truthy("SESSION_DIGEST_DEDUP_ENABLED", True):
        return False
    if len(buf) < _digest_dedup_min_turns():
        return False
    excerpts = [str(x.get("user_excerpt") or "").strip().lower() for x in buf if x.get("user_excerpt")]
    if len(excerpts) < _digest_dedup_min_turns():
        return False
    first = excerpts[0][:80]
    if not first:
        return False
    same = sum(1 for e in excerpts if e[:80] == first or (len(e) > 20 and first[:40] in e))
    if same >= max(_digest_dedup_min_turns() - 1, int(len(excerpts) * 0.7)):
        logger.info(
            "session_digest: skip flush (same-topic buffer turns=%s)",
            len(buf),
            extra={"gemma_event": "session_digest_dedup_skip"},
        )
        return True
    return False


def _flush_user(uid: str, buf: List[Dict[str, Any]]) -> None:
    if not buf:
        return
    path = default_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except OSError:
        pass
    ok = sum(1 for x in buf if x.get("outcome") == "ok")
    fb = sum(1 for x in buf if x.get("outcome") == "fallback")
    cl = sum(1 for x in buf if x.get("outcome") == "clarify")
    er = sum(1 for x in buf if x.get("outcome") == "error")
    fl = sum(1 for x in buf if x.get("outcome") == "failure")
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": uid,
        "turns": len(buf),
        "ok": ok,
        "fallback": fb,
        "clarify": cl,
        "error": er,
        "failure": fl,
        "samples": buf[:8],
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("session_digest flush: %s", e)
    finally:
        buf.clear()
