"""Аудит расхождения маршрута (embedding/classifier vs финальный профиль) — только лог, не override."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def route_semantic_audit_enabled() -> bool:
    raw = (os.getenv("ROUTE_SEMANTIC_AUDIT_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def route_semantic_audit_live_override_enabled() -> bool:
    """Запрещено при brain-centric: embedding не должен менять профиль на ходу."""
    raw = (os.getenv("ROUTE_SEMANTIC_AUDIT_LIVE_OVERRIDE") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_semantic_audit_note(
    *,
    user_text: str,
    final_profile: str,
    classifier_profile: str = "",
    classifier_confidence: float = 0.0,
    router_source: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Запись в router_route_audit.semantic_audit — без смены final_profile.
    """
    if not route_semantic_audit_enabled():
        return None
    fp = (final_profile or "standard").strip()
    cp = (classifier_profile or "").strip()
    if not cp or cp == fp:
        return None
    note = {
        "final_profile": fp,
        "classifier_profile": cp,
        "classifier_confidence": round(float(classifier_confidence or 0.0), 3),
        "router_source": (router_source or "").strip(),
        "mismatch": True,
        "live_override": route_semantic_audit_live_override_enabled(),
    }
    if note["live_override"]:
        logger.warning(
            "ROUTE_SEMANTIC_AUDIT_LIVE_OVERRIDE=true — не рекомендуется на prod (brain-centric)"
        )
    return note
