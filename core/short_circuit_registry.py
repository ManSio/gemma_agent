"""Единый реестр planner short-circuits → TurnContract.short_circuit."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, Dict[str, Any]] = {
    "weather_direct": {"lane": "FACT", "intent": "weather"},
    "weather_followup": {"lane": "FACT", "intent": "weather"},
    "geo_nearby": {"lane": "FACT", "intent": "geo"},
    "telegram_location": {"lane": "FACT", "intent": "geo"},
    "news_direct": {"lane": "FACT", "intent": "news"},
    "news_web_search": {"lane": "FACT", "intent": "news"},
    "news_item_direct": {"lane": "FACT", "intent": "news"},
    "referential_math": {"lane": "FACT", "intent": "math"},
    "affirmative_search": {"lane": "FACT", "intent": "search"},
    "nl_reminder": {"lane": "FACT", "intent": "reminder"},
    "nl_cancel_reminder": {"lane": "FACT", "intent": "reminder"},
    "nl_weekly_schedule": {"lane": "FACT", "intent": "schedule"},
    "user_facts_identity_nl": {"lane": "FACT", "intent": "identity"},
    "session_meta_recall_nl": {"lane": "DIALOGUE", "intent": "recall"},
    "dialog_recall_nl": {"lane": "DIALOGUE", "intent": "recall"},
    "article_thread_followup_nl": {"lane": "DIALOGUE", "intent": "article"},
    "pre_llm": {"lane": "DIALOGUE", "intent": "general"},
}


def register_short_circuit(shortcut_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """Зарегистрировать или обновить short-circuit (runtime plugins)."""
    sid = (shortcut_id or "").strip()
    if not sid:
        return
    base = dict(_REGISTRY.get(sid) or {})
    if isinstance(meta, dict):
        base.update(meta)
    base.setdefault("id", sid)
    _REGISTRY[sid] = base


def lookup_short_circuit(shortcut_id: str) -> Optional[Dict[str, Any]]:
    """Метаданные short-circuit по id."""
    sid = (shortcut_id or "").strip()
    if not sid:
        return None
    ent = _REGISTRY.get(sid)
    return dict(ent) if ent else None


def record_short_circuit_use(
    shortcut_id: str,
    *,
    input_meta: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
) -> None:
    """Записать использование SC в turn_contract + метрики."""
    sid = (shortcut_id or "").strip()
    if not sid:
        return
    ent = lookup_short_circuit(sid) or {"id": sid}
    MONITOR.inc("short_circuit_registry_use_total")
    MONITOR.inc(f"short_circuit_{sid}_total")
    if not isinstance(input_meta, dict):
        return
    try:
        from core.turn_delivery_store import patch_turn_contract_shortcut

        patch_turn_contract_shortcut(
            input_meta,
            sid,
            profile=str(ent.get("profile") or ""),
        )
        tc = input_meta.get("turn_contract")
        if isinstance(tc, dict):
            tc = dict(tc)
            if ent.get("lane"):
                tc["lane"] = str(ent["lane"])
            tc["short_circuit_registry"] = sid
            input_meta["turn_contract"] = tc
    except Exception as e:
        logger.debug("record_short_circuit_use: %s", e)
    if trace_id:
        input_meta.setdefault("short_circuit_trace", trace_id[:64])


def all_registered_shortcuts() -> Dict[str, Dict[str, Any]]:
    """Копия реестра для тестов и replay."""
    return {k: dict(v) for k, v in _REGISTRY.items()}
