"""
Подключение известных ядру pending-источников к единому реестру.

Импортируется один раз на старте процесса (например, из core/main.py
или из core/input_layer.py при инициализации).
"""
from __future__ import annotations

import logging

from core.pending_flow import register_pending_source

logger = logging.getLogger(__name__)


def _clear_math_clarify(user_id: str, chat_id: str) -> bool:
    try:
        from core.math_clarify_pending import pop_pending
    except Exception:
        return False
    try:
        return pop_pending(user_id, chat_id) is not None
    except Exception as exc:
        logger.debug("pending_flow: math_clarify clear failed: %s", exc)
        return False


def _clear_user_image(user_id: str, chat_id: str) -> bool:
    try:
        from core.user_image_pending import pop_pending_images
    except Exception:
        return False
    try:
        rows = pop_pending_images(user_id, chat_id, limit=10) or []
        return bool(rows)
    except Exception as exc:
        logger.debug("pending_flow: user_image clear failed: %s", exc)
        return False


def _clear_bug_report(user_id: str, chat_id: str) -> bool:
    try:
        from core.user_bug_report import clear_fn
    except Exception:
        return False
    try:
        return clear_fn(user_id, chat_id)
    except Exception as exc:
        logger.debug("pending_flow: bug_report clear failed: %s", exc)
        return False


_INSTALLED = False


def install() -> None:
    """Идемпотентная регистрация всех known pending-источников ядра."""
    global _INSTALLED
    if _INSTALLED:
        return
    register_pending_source("math_clarify", _clear_math_clarify)
    register_pending_source("user_image", _clear_user_image)
    register_pending_source("bug_report", _clear_bug_report)
    _INSTALLED = True


__all__ = ["install"]
