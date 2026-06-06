"""Отдельный vision-вызов и пульс прогресса в Telegram."""

from __future__ import annotations

import logging
import os
from typing import List, Tuple

from core.llm_tiered import llm_generate_tiered
from core.resilience import with_timeout

from core.brain.env import env_flag
from core.brain.runtime import _llm
from core.brain.text_helpers import safe_text

logger = logging.getLogger(__name__)


def brain_default_vision_system_prompt() -> str:
    return (
        "Ты видишь изображение так же, как человек. Отвечай по-русски кратко и по делу: "
        "объекты, текст на кадре, цвета и контекст. Не выдумывай того, чего не видно."
    )


async def brain_run_vision_precaption(
    *,
    user_text: str,
    vision_parts: List[Tuple[str, str]],
) -> str:
    """Короткий vision-вызов отдельно от агентского промпта (меньше токенов и стабильнее, как в v1)."""
    v_sys = (os.getenv("BRAIN_VISION_SYSTEM_PROMPT") or "").strip() or brain_default_vision_system_prompt()
    v_user = (user_text or "").strip() or "Что на изображении? Опиши объекты и любой текст на кадре."
    if len(v_user) > 1200:
        v_user = v_user[:1200] + "…"
    try:
        v_to = float(os.getenv("BRAIN_VISION_PRECAPTION_TIMEOUT_SEC", "90"))
    except ValueError:
        v_to = 90.0
    try:
        v_tok = int(os.getenv("BRAIN_VISION_MAX_TOKENS", "512"))
    except ValueError:
        v_tok = 512
    try:
        v_temp = float(os.getenv("BRAIN_VISION_TEMPERATURE", "0.35"))
    except ValueError:
        v_temp = 0.35
    vm = (os.getenv("OPENROUTER_MODEL_VISION") or "").strip() or None
    if env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
        res = await llm_generate_tiered(
            _llm,
            tag="vision_precaption",
            prompt=v_user,
            system_prompt=v_sys,
            max_tokens=v_tok,
            temperature=v_temp,
            vision_image_parts=vision_parts,
            model=vm,
            base_timeout=max(30.0, min(v_to, 180.0)),
        )
    else:
        res = await with_timeout(
            _llm.generate(
                prompt=v_user,
                system_prompt=v_sys,
                max_tokens=v_tok,
                temperature=v_temp,
                vision_image_parts=vision_parts,
                model=vm,
            ),
            timeout_sec=max(30.0, min(v_to, 180.0)),
            tag="vision_precaption",
        )
    if res.get("error"):
        logger.warning("[brain] vision_precaption: %s", res.get("error"))
        return ""
    return safe_text(res.get("content", ""))


async def brain_progress(text: str, *, force: bool = False) -> None:
    """Обновить статус-сообщение в Telegram, если input_layer выставил arm."""
    try:
        from core.telegram_progress import telegram_progress_pulse

        await telegram_progress_pulse(text, force=force)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'vision_llm', e, exc_info=True)