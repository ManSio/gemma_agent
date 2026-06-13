"""Реестр слотов диалога: контракт accepts_turn на kind (как profile_registry для brain)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

SLOT_WEATHER_CITY = "weather_await_city"
SLOT_ARTICLE_THREAD = "article_thread"
SLOT_SPATIAL_PROJECT = "spatial_project"

AcceptsTurnFn = Callable[[str, Any, Optional[Dict[str, Any]]], bool]


def _weather_accepts_turn(
    user_text: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> bool:
    """Слот погоды живёт только на запросе погоды или коротком топониме."""
    from core.dialogue_slots import _turn_binds_weather_slot

    return _turn_binds_weather_slot(user_text)


def _article_accepts_turn(
    user_text: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> bool:
    """Слот статьи — пока реплика в нити статьи или follow-up по paste."""
    from core.dialogue_slots import user_refers_to_article_thread

    try:
        from core.article_thread_followup import (
            looks_like_article_thread_clarification,
            looks_like_article_thread_opinion_followup,
        )

        if looks_like_article_thread_clarification(user_text):
            return True
        if looks_like_article_thread_opinion_followup(user_text):
            return True
    except Exception as e:
        logger.debug("slot_registry article opinion/clarify: %s", e)
    if user_refers_to_article_thread(user_text, recent_dialogue):
        return True
    low = (user_text or "").strip().lower()
    if len(low) > 140:
        return False
    try:
        from core.dialogue_slots import _ARTICLE_FOLLOWUP_RE, _recent_has_pasted_article

        if _ARTICLE_FOLLOWUP_RE.search(low) and _recent_has_pasted_article(recent_dialogue):
            return True
    except Exception as e:
        logger.debug("slot_registry article accepts: %s", e)
    return False


def _spatial_accepts_turn(
    user_text: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> bool:
    """Слот планировки — сверка/правки по проекту, не общий чат."""
    low = (user_text or "").strip().lower()
    if not low:
        return False
    try:
        from core.spatial_design.feedback import classify_feedback

        fb = classify_feedback(user_text)
        if fb in {"confirm", "reject", "edit", "question"}:
            return True
    except Exception as e:
        logger.debug("slot_registry spatial accepts: %s", e)
    if any(k in low for k in ("планиров", "мм", "комнат", "кухн", "спальн", "санузел")):
        return True
    return len(low) <= 24


@dataclass(frozen=True)
class SlotConfig:
    """Конфигурация одного dialogue slot."""

    kind: str
    footer_label: str
    default_turns: int
    accepts_turn: AcceptsTurnFn


def _weather_turns() -> int:
    try:
        return max(1, min(10, int((os.getenv("DIALOGUE_SLOT_WEATHER_TURNS") or "3").strip())))
    except ValueError:
        return 3


def _article_turns() -> int:
    try:
        return max(2, min(16, int((os.getenv("DIALOGUE_SLOT_ARTICLE_TURNS") or "8").strip())))
    except ValueError:
        return 8


def _spatial_turns() -> int:
    try:
        return max(4, min(24, int((os.getenv("DIALOGUE_SLOT_SPATIAL_TURNS") or "12").strip())))
    except ValueError:
        return 12


_SLOT_REGISTRY: Dict[str, SlotConfig] = {
    SLOT_WEATHER_CITY: SlotConfig(
        kind=SLOT_WEATHER_CITY,
        footer_label="погода",
        default_turns=_weather_turns(),
        accepts_turn=_weather_accepts_turn,
    ),
    SLOT_ARTICLE_THREAD: SlotConfig(
        kind=SLOT_ARTICLE_THREAD,
        footer_label="статья",
        default_turns=_article_turns(),
        accepts_turn=_article_accepts_turn,
    ),
    SLOT_SPATIAL_PROJECT: SlotConfig(
        kind=SLOT_SPATIAL_PROJECT,
        footer_label="планировка",
        default_turns=_spatial_turns(),
        accepts_turn=_spatial_accepts_turn,
    ),
}


def slot_registry_enabled() -> bool:
    """Включён ли реестр контрактов слотов."""
    raw = (os.getenv("SLOT_REGISTRY_ENABLED") or "true").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def get_slot_config(kind: str) -> Optional[SlotConfig]:
    """Вернуть конфиг слота по kind."""
    k = (kind or "").strip()
    if not k:
        return None
    return _SLOT_REGISTRY.get(k)


def slot_accepts_turn(
    kind: str,
    user_text: str,
    recent_dialogue: Any = None,
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """Контракт: принимает ли реплика ожидаемый ввод для kind."""
    if not slot_registry_enabled():
        return kind != SLOT_WEATHER_CITY or _weather_accepts_turn(
            user_text, recent_dialogue, persisted
        )
    cfg = get_slot_config(kind)
    if cfg is None:
        return True
    try:
        return bool(cfg.accepts_turn(user_text, recent_dialogue, persisted))
    except Exception as e:
        logger.debug("slot_accepts_turn %s: %s", kind, e)
        return False


def slot_footer_label(kind: str) -> str:
    """Человеческая подпись слота для footer."""
    cfg = get_slot_config(kind)
    if cfg:
        return cfg.footer_label
    return kind.replace("_", " ")


def slot_default_turns(kind: str) -> Optional[int]:
    """TTL хода по умолчанию для kind."""
    cfg = get_slot_config(kind)
    return cfg.default_turns if cfg else None


def registered_slot_kinds() -> tuple[str, ...]:
    """Все зарегистрированные kind."""
    return tuple(_SLOT_REGISTRY.keys())
