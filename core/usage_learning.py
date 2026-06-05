"""
Lightweight usage learning for autopilot insights.

Collects anonymous aggregates (hour, intents, modules, short query shapes).
Persists to RESILIENCE_RUNTIME_DIR/usage_learning_state.json (configurable).
Digest checkpoint: usage_learning_digest_checkpoint.json for trend deltas.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.report_i18n import system_status_lamp

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loaded = False
_by_hour: Dict[int, int] = defaultdict(int)
_by_intent: Dict[str, int] = defaultdict(int)
_by_module: Dict[str, int] = defaultdict(int)
_top_queries: Dict[str, int] = defaultdict(int)
_total = 0
_last_activity_unix: float = 0.0
_since_persist = 0

STATE_VERSION = 1


def _runtime_dir() -> Path:
    return Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))


def state_path() -> Path:
    raw = (os.getenv("USAGE_LEARNING_STATE_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _runtime_dir() / "usage_learning_state.json"


def digest_checkpoint_path() -> Path:
    raw = (os.getenv("USAGE_LEARNING_DIGEST_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _runtime_dir() / "usage_learning_digest_checkpoint.json"


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _serialize_locked() -> Dict[str, Any]:
    return {
        "v": STATE_VERSION,
        "total": _total,
        "last_activity_unix": _last_activity_unix,
        "by_hour": {str(k): int(v) for k, v in _by_hour.items()},
        "by_intent": {str(k): int(v) for k, v in _by_intent.items()},
        "by_module": {str(k): int(v) for k, v in _by_module.items()},
        "by_query": {str(k): int(v) for k, v in _top_queries.items()},
    }


def _hydrate_from_json(raw: Dict[str, Any]) -> None:
    global _total, _last_activity_unix
    _total = int(raw.get("total") or 0)
    _last_activity_unix = float(raw.get("last_activity_unix") or 0)
    _by_hour.clear()
    bh = raw.get("by_hour")
    if isinstance(bh, dict):
        for k, v in bh.items():
            try:
                _by_hour[int(k)] = int(v)
            except (TypeError, ValueError):
                pass
    _by_intent.clear()
    bi = raw.get("by_intent")
    if isinstance(bi, dict):
        for k, v in bi.items():
            try:
                _by_intent[str(k)] = int(v)
            except (TypeError, ValueError):
                pass
    _by_module.clear()
    bm = raw.get("by_module")
    if isinstance(bm, dict):
        for k, v in bm.items():
            try:
                _by_module[str(k)] = int(v)
            except (TypeError, ValueError):
                pass
    _top_queries.clear()
    bq = raw.get("by_query")
    if not isinstance(bq, dict):
        bq = raw.get("top_queries")  # legacy name
    if isinstance(bq, dict):
        for k, v in bq.items():
            try:
                _top_queries[str(k)] = int(v)
            except (TypeError, ValueError):
                pass


def ensure_loaded() -> None:
    """Idempotent load from disk (call at startup)."""
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        p = state_path()
        if not p.is_file():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _hydrate_from_json(raw)
        except Exception as e:
            logger.warning("usage_learning load failed: %s", e)


def persist_state() -> None:
    """Flush current aggregates to disk."""
    ensure_loaded()
    with _lock:
        payload = _serialize_locked()
    try:
        _atomic_write(state_path(), payload)
    except Exception as e:
        logger.warning("usage_learning persist_state: %s", e)


def reset_for_tests() -> None:
    """Сброс памяти (юнит-тесты)."""
    global _loaded, _total, _last_activity_unix, _since_persist
    with _lock:
        _loaded = False
        _total = 0
        _last_activity_unix = 0.0
        _since_persist = 0
        _by_hour.clear()
        _by_intent.clear()
        _by_module.clear()
        _top_queries.clear()


def _norm_query(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = re.sub(r"\d{2,}", "#", t)
    t = re.sub(r"\s+", " ", t)
    return t[:80]


def _save_every_n() -> int:
    try:
        return max(1, int(os.getenv("USAGE_LEARNING_SAVE_EVERY", "25")))
    except ValueError:
        return 25


def record_usage(text: str, intent: str, module: str) -> None:
    global _total, _since_persist, _last_activity_unix
    ensure_loaded()
    flush_payload: Optional[Dict[str, Any]] = None
    q = _norm_query(text)
    hr = datetime.now(timezone.utc).hour
    with _lock:
        _total += 1
        _since_persist += 1
        _by_hour[int(hr)] += 1
        if intent:
            _by_intent[str(intent)] += 1
        if module:
            _by_module[str(module)] += 1
        if q:
            _top_queries[q] += 1
        _last_activity_unix = time.time()
        n = _save_every_n()
        if _since_persist >= n:
            _since_persist = 0
            flush_payload = _serialize_locked()
    if flush_payload is not None:
        try:
            _atomic_write(state_path(), flush_payload)
        except Exception as e:
            logger.warning("usage_learning persist: %s", e)


def snapshot() -> Dict[str, Any]:
    ensure_loaded()
    with _lock:
        top_hours = sorted(_by_hour.items(), key=lambda x: (-x[1], x[0]))[:6]
        top_intents = sorted(_by_intent.items(), key=lambda x: -x[1])[:8]
        top_modules = sorted(_by_module.items(), key=lambda x: -x[1])[:8]
        top_queries = sorted(_top_queries.items(), key=lambda x: -x[1])[:8]
    return {
        "total_events": _total,
        "top_hours_utc": [{"hour": h, "count": c} for h, c in top_hours],
        "top_intents": [{"intent": k, "count": v} for k, v in top_intents],
        "top_modules": [{"module": k, "count": v} for k, v in top_modules],
        "top_queries": [{"query": q, "count": c} for q, c in top_queries],
    }


def insights() -> List[str]:
    snap = snapshot()
    out: List[str] = []
    hours = snap.get("top_hours_utc") or []
    if hours:
        hs = ", ".join(f"{x['hour']:02d}:00({x['count']})" for x in hours[:3] if isinstance(x, dict))
        out.append(f"Пиковые часы: {hs}.")
    intents = snap.get("top_intents") or []
    if intents:
        top = intents[0]
        if isinstance(top, dict):
            out.append(
                f"Чаще всего намерение «{top.get('intent')}» — {top.get('count')} раз (intent)."
            )
    mods = snap.get("top_modules") or []
    if mods:
        top = mods[0]
        if isinstance(top, dict):
            out.append(
                f"Основная нагрузка на модуль «{top.get('module')}» — {top.get('count')} раз."
            )
    if not out:
        out.append("Недостаточно данных для поведенческих инсайтов.")
    return out


def seconds_since_activity() -> float:
    """Секунды с последнего record_usage (0 если ещё не было событий)."""
    ensure_loaded()
    with _lock:
        if _last_activity_unix <= 0:
            return float("inf")
        return max(0.0, time.time() - _last_activity_unix)


def read_digest_checkpoint() -> Dict[str, Any]:
    p = digest_checkpoint_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _counts_map(rows: List[Any], key_field: str) -> Dict[str, int]:
    m: Dict[str, int] = {}
    for x in rows:
        if not isinstance(x, dict):
            continue
        k = x.get(key_field)
        if k is None:
            continue
        try:
            m[str(k)] = int(x.get("count") or 0)
        except (TypeError, ValueError):
            m[str(k)] = 0
    return m


def _trend_lines(
    label: str,
    key_field: str,
    baseline_rows: List[Any],
    current_rows: List[Any],
) -> List[str]:
    old_m = _counts_map(baseline_rows, key_field)
    lines: List[str] = []
    for x in current_rows[:5]:
        if not isinstance(x, dict):
            continue
        name = str(x.get(key_field) or "")
        try:
            c = int(x.get("count") or 0)
        except (TypeError, ValueError):
            c = 0
        oc = old_m.get(name, 0)
        if oc:
            d = c - oc
            if d > 0:
                lines.append(f"• {label} «{name}»: {c} (+{d} к прошлому дайджесту)")
            elif d < 0:
                lines.append(f"• {label} «{name}»: {c} ({d} к прошлому дайджесту)")
            else:
                lines.append(f"• {label} «{name}»: {c} (без изменений)")
        else:
            lines.append(f"• {label} «{name}»: {c} (новый в топе)")
    return lines


def build_digest_payload(*, slot_label: str, orchestrator: Any = None) -> Dict[str, Any]:
    """Снимок + дельты к последнему дайджесту (чекпоинт не обновляет)."""
    snap = snapshot()
    cp = read_digest_checkpoint()
    baseline_total = int(cp.get("baseline_total", 0) or 0)
    delta = max(0, int(snap.get("total_events") or 0) - baseline_total)
    base = cp.get("baseline") if isinstance(cp.get("baseline"), dict) else {}
    b_int = base.get("top_intents") if isinstance(base.get("top_intents"), list) else []
    b_mod = base.get("top_modules") if isinstance(base.get("top_modules"), list) else []
    b_q = base.get("top_queries") if isinstance(base.get("top_queries"), list) else []
    trends: List[str] = []
    trends.extend(_trend_lines("намерение", "intent", b_int, snap.get("top_intents") or []))
    trends.extend(_trend_lines("модуль", "module", b_mod, snap.get("top_modules") or []))
    trends.extend(_trend_lines("формулировка", "query", b_q, snap.get("top_queries") or []))
    lamp = system_status_lamp(orchestrator) if orchestrator is not None else None
    return {
        "slot": slot_label,
        "snapshot": snap,
        "delta_events": delta,
        "baseline_total": baseline_total,
        "trends": trends[:12],
        "insights": insights(),
        "lamp": lamp,
    }


def commit_digest_checkpoint(slot: str) -> None:
    snap = snapshot()
    payload = {
        "last_digest_slot": slot,
        "baseline_total": int(snap.get("total_events") or 0),
        "baseline": {
            "top_intents": (snap.get("top_intents") or [])[:5],
            "top_modules": (snap.get("top_modules") or [])[:5],
            "top_hours_utc": (snap.get("top_hours_utc") or [])[:4],
            "top_queries": (snap.get("top_queries") or [])[:5],
        },
    }
    try:
        _atomic_write(digest_checkpoint_path(), payload)
    except Exception as e:
        logger.warning("usage_learning digest checkpoint: %s", e)


def digest_slot_utc(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H")


def should_emit_digest_this_hour(
    *,
    now: datetime,
    digest_hours: List[int],
) -> Tuple[bool, str]:
    """Один слот на календарный час UTC; не дублировать для того же last_digest_slot."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    if now.hour not in digest_hours:
        return False, digest_slot_utc(now)
    slot = digest_slot_utc(now)
    cp = read_digest_checkpoint()
    if str(cp.get("last_digest_slot") or "") == slot:
        return False, slot
    return True, slot


def parse_int_list(raw: str, *, default: List[int]) -> List[int]:
    out: List[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out if out else list(default)
