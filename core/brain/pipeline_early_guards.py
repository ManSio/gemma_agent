"""Ранние выходы call_brain: пустой ввод, silent image, glyph/heavy/injection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.brain.constants import SILENT_IMAGE_USER_PROMPT
from core.brain.env import env_flag
from core.brain.pipeline_postprocess import persona_apply_polished as _persona_apply_polished
from core.brain.runtime import _memory
from core.brain.text_helpers import (
    natural_fallback_response as _natural_fallback_response,
    safe_text as _safe_text,
    user_input_heavy_for_llm as _user_input_heavy_for_llm,
    user_requests_prompt_exfiltration as _user_requests_prompt_exfiltration,
    user_requests_prompt_injection_playback as _user_requests_prompt_injection_playback,
)

logger = logging.getLogger(__name__)


@dataclass
class BrainInputGate:
    """Итог ранних guard'ов до загрузки памяти."""

    user_text: str
    skip_memory_writes: bool
    skip_mem_fetch: bool
    early_reply: Optional[str] = None


def compute_memory_skip_flags(
    context: Dict[str, Any],
    *,
    need_memory: bool,
) -> Tuple[bool, bool]:
    ctx = context if isinstance(context, dict) else {}
    dedup_enabled = env_flag("BRAIN_DEDUP_MEMORY", default=True)
    memory_managed = bool(ctx.get("memory_managed")) or (
        "mem0_facts" in ctx and isinstance(ctx.get("mem0_facts"), list)
    )
    skip_memory_writes = dedup_enabled and memory_managed
    skip_mem_fetch = bool(ctx.get("brain_skip_memory_fetch"))
    if need_memory:
        skip_mem_fetch = False
    return skip_memory_writes, skip_mem_fetch


def resolve_user_text_with_file_context(
    user_text: str,
    file_context: Optional[Dict[str, Any]],
) -> str:
    fc = file_context if isinstance(file_context, dict) else {}
    if (
        not (user_text or "").strip()
        and fc.get("file_type") == "image"
        and isinstance(fc.get("local_path"), str)
        and fc.get("local_path").strip()
        and not fc.get("error")
    ):
        return SILENT_IMAGE_USER_PROMPT
    return user_text


async def _polish_and_persist_early_reply(
    user_id: str,
    reply: str,
    *,
    skip_memory_writes: bool,
) -> str:
    out = reply
    try:
        out = _persona_apply_polished(user_id, out)
        if not skip_memory_writes:
            await _memory.on_after_response(user_id, out)
    except Exception as e:
        logger.debug("early_guard persist: %s", e, exc_info=True)
    return out if _safe_text(out) else out


async def try_early_exit_reply(
    *,
    user_id: str,
    user_text: str,
    skip_memory_writes: bool,
) -> Optional[str]:
    """Один символ, слишком тяжёлый ввод, prompt injection — без LLM."""
    ut_work = (user_text or "").strip()
    if len(ut_work) == 1 and ut_work.isalpha():
        reply = _natural_fallback_response("single_glyph", user_id, user_text)
        return await _polish_and_persist_early_reply(user_id, reply, skip_memory_writes=skip_memory_writes)

    skip_heavy_guard = False
    try:
        from core.brain.text_helpers import looks_like_pasted_news_article

        if looks_like_pasted_news_article(user_text):
            skip_heavy_guard = True
    except Exception as e:
        logger.debug("early_guard paste check: %s", e)

    if not skip_heavy_guard and _user_input_heavy_for_llm(user_text):
        reply = _natural_fallback_response("empty_llm", user_id, user_text)
        return await _polish_and_persist_early_reply(user_id, reply, skip_memory_writes=skip_memory_writes)

    if _user_requests_prompt_injection_playback(user_text) or _user_requests_prompt_exfiltration(user_text):
        reply = _natural_fallback_response("injection_playback", user_id, user_text)
        return await _polish_and_persist_early_reply(user_id, reply, skip_memory_writes=skip_memory_writes)

    return None


async def apply_early_input_guards(
    *,
    user_id: str,
    user_text: str,
    context: Dict[str, Any],
    need_memory: bool,
    file_context: Optional[Dict[str, Any]] = None,
) -> BrainInputGate:
    """
    Silent image → подстановка промпта; пустой текст; ранние exit-guards.
    Вызывать после роутинга и setup_early_brain_session.
    """
    resolved = resolve_user_text_with_file_context(user_text, file_context)
    if not (resolved or "").strip():
        empty = _natural_fallback_response("empty", user_id)
        return BrainInputGate(
            user_text=resolved,
            skip_memory_writes=False,
            skip_mem_fetch=False,
            early_reply=empty,
        )

    skip_memory_writes, skip_mem_fetch = compute_memory_skip_flags(context, need_memory=need_memory)
    early = await try_early_exit_reply(
        user_id=user_id,
        user_text=resolved,
        skip_memory_writes=skip_memory_writes,
    )
    return BrainInputGate(
        user_text=resolved,
        skip_memory_writes=skip_memory_writes,
        skip_mem_fetch=skip_mem_fetch,
        early_reply=early,
    )
