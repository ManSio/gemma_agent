"""Догенерация оборванного ответа (finish_reason=length / обрыв на слове)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def incomplete_continue_enabled() -> bool:
    raw = (os.getenv("BRAIN_INCOMPLETE_CONTINUE_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def incomplete_continue_min_user_chars() -> int:
    try:
        return max(40, int((os.getenv("BRAIN_INCOMPLETE_CONTINUE_MIN_USER_CHARS") or "120").strip()))
    except ValueError:
        return 120


async def maybe_continue_incomplete_reply(
    *,
    llm: Any,
    user_text: str,
    reply: str,
    task_tier: str,
    system_prompt: str,
    llm_session_id: str,
    telemetry_extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Один короткий continue-вызов, если ответ похож на обрыв."""
    if not incomplete_continue_enabled():
        return reply
    from core.input_layer import _reply_suspect_incomplete

    body = (reply or "").strip()
    if not body or not _reply_suspect_incomplete(body):
        return reply
    if len((user_text or "").strip()) < incomplete_continue_min_user_chars():
        return reply
    tier = (task_tier or "").strip().lower()
    if tier not in ("deep", "nested", "shallow", "standard"):
        return reply
    if "продолжи" in body.lower() or "обрезан лимитом" in body.lower():
        return reply
    try:
        from core.llm_tiered import llm_generate_tiered

        cont_prompt = (
            f"Пользователь спросил:\n{user_text[:2000]}\n\n"
            f"Твой предыдущий ответ оборвался на:\n{body[-800:]}\n\n"
            "Продолжи с места обрыва. Не повторяй уже написанное. "
            "Заверши мысль и ответь по сути на весь вопрос."
        )
        out = await llm_generate_tiered(
            llm,
            tag="llm_incomplete_continue",
            prompt=cont_prompt,
            system_prompt=system_prompt,
            max_tokens=2500,
            temperature=0.35,
            task_tier=task_tier,
            telemetry_tag="brain_incomplete_continue",
            telemetry_extra=telemetry_extra if isinstance(telemetry_extra, dict) else None,
            session_id=llm_session_id,
            conversation_id=llm_session_id,
        )
        chunk = str(out.get("content") or "").strip()
        if chunk and len(chunk) > 20:
            from core.monitoring import MONITOR

            MONITOR.inc("brain_incomplete_continue_total")
            return body.rstrip() + "\n\n" + chunk
    except Exception as e:
        logger.debug("incomplete_continue: %s", e)
    return reply
