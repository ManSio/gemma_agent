"""
Long-Term Episodic Memory (Autonomy 3.0).
Stores events (not facts) with timestamp, type, and brief description.
Derives insights from event patterns.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

EPISODIC_MEMORY_VERSION = "1.0.0"

_MAX_EVENTS = 2000
_INSIGHT_THRESHOLD = 3

_EVENT_TYPES: Set[str] = {
    "tool_error",
    "tool_success",
    "user_query",
    "goal_started",
    "goal_completed",
    "replan_applied",
    "self_healing",
    "tool_chain_used",
    "context_reset",
}


def _store_path() -> Path:
    return Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime")) / "episodic_memory.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_event(event_type: str, description: str, *, user_id: Optional[str] = None) -> None:
    """Append a timestamped event to episodic memory."""
    if event_type not in _EVENT_TYPES:
        logger.debug("episodic_memory: unknown event_type=%s", event_type)
        return
    entry = {
        "ts": _now(),
        "type": event_type,
        "desc": (description or "")[:500],
    }
    if user_id:
        entry["user_id"] = str(user_id)
    try:
        sp = _store_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        with open(sp, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("episodic_memory: write error %s", e)


def _read_tail(n: int = 400) -> List[Dict[str, Any]]:
    sp = _store_path()
    if not sp.exists():
        return []
    try:
        lines = sp.read_text(encoding="utf-8").strip().split("\n")
        tail = lines[-n:]
    except Exception:
        return []
    events: List[Dict[str, Any]] = []
    for line in tail:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def recent_events(n: int = 100) -> List[Dict[str, Any]]:
    return _read_tail(n)


def _derive_insights() -> List[str]:
    events = _read_tail(600)
    insights: List[str] = []

    tool_errors: Dict[str, int] = {}
    recent_tool_success: Dict[str, int] = {}
    for e in events:
        t = e.get("type", "")
        if t == "tool_error":
            name = (e.get("desc") or "").split(":")[0].strip()
            if name:
                tool_errors[name] = tool_errors.get(name, 0) + 1
        if t == "tool_success":
            name = (e.get("desc") or "").split(":")[0].strip()
            if name:
                recent_tool_success[name] = recent_tool_success.get(name, 0) + 1

    for tool_name, cnt in tool_errors.items():
        if cnt >= _INSIGHT_THRESHOLD:
            insights.append(f"{tool_name} часто падает ({cnt} ошибок) → использовать fallback")

    user_frequent_tools: Dict[str, int] = {}
    for e in events:
        if e.get("type") == "tool_success":
            name = (e.get("desc") or "").split(":")[0].strip()
            uid = e.get("user_id", "")
            if name and uid:
                key = f"{uid}:{name}"
                user_frequent_tools[key] = user_frequent_tools.get(key, 0) + 1

    for key, cnt in user_frequent_tools.items():
        if cnt >= _INSIGHT_THRESHOLD:
            uid, tool = key.split(":", 1)
            insights.append(f"пользователь {uid[:8]} часто использует {tool} → включить proactive-help")

    return insights


def get_insights() -> List[str]:
    try:
        return _derive_insights()
    except Exception as e:
        logger.debug("episodic_memory insights: %s", e)
        return []


def _trim_to_max() -> None:
    sp = _store_path()
    if not sp.exists():
        return
    try:
        lines = sp.read_text(encoding="utf-8").strip().split("\n")
        if len(lines) <= _MAX_EVENTS:
            return
        keep = lines[-_MAX_EVENTS:]
        sp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'memory_store', e, exc_info=True)
def maintenance() -> None:
    _trim_to_max()
    try:
        insights = get_insights()
        if insights:
            logger.info("episodic_memory insights: %s", "; ".join(insights[:5]))
    except Exception as e:
        logger.debug('%s optional failed: %s', 'memory_store', e, exc_info=True)