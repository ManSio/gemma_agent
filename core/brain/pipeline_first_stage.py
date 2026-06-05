"""Первый проход LLM: retry TOOL_CALL и разбор маркера."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from core.brain.cot_strip import strip_leaked_cot as _strip_leaked_cot
from core.brain.env import env_flag
from core.brain.kv_debug_logger import record_kv_trace as _record_kv_trace
from core.brain.runtime import _llm
from core.brain.session_stickiness import increment_turn as _increment_kv_turn
from core.brain.text_helpers import (
    looks_like_repetition_glitch as _looks_like_repetition_glitch,
    parse_tool_call as _parse_tool_call,
    safe_text as _safe_text,
)
from core.brain.tool_call_support import (
    describe_tool_call_retry_issue,
    text_before_tool_call,
)
from core.error_analysis import record_error_event
from core.llm_tiered import llm_generate_tiered
from core.monitoring import MONITOR
from core.resilience import DEFAULT_TIMEOUT_SEC, with_timeout

logger = logging.getLogger(__name__)


@dataclass
class FirstStageOutcome:
    first: Dict[str, Any] = field(default_factory=dict)
    first_content: str = ""
    has_llm_error: bool = False


def max_tool_call_retries() -> int:
    try:
        n = int((os.getenv("BRAIN_TOOL_CALL_RETRY") or "2").strip())
    except ValueError:
        n = 1
    return max(0, min(n, 3))


def normalize_first_stage_content(
    raw: str,
    *,
    brain_profile: str,
    prof_primary: Any,
) -> str:
    text = _safe_text(raw)
    if brain_profile in ("code_generation", "code_debug"):
        from core.brain.code_empty_recovery import strip_cot_for_code

        return strip_cot_for_code(text)
    return _strip_leaked_cot(
        text,
        extra_markers_en=getattr(prof_primary, "cot_extra_markers_en", None),
        extra_markers_ru=getattr(prof_primary, "cot_extra_markers_ru", None),
    )


def resolve_tool_calls_from_first_content(
    first_content: str,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    tool_call = _parse_tool_call(first_content)
    batched: List[Dict[str, Any]] = []
    try:
        from core.brain.text_helpers import parse_tool_calls_batched, tools_batch_enabled

        if tools_batch_enabled():
            batched = parse_tool_calls_batched(first_content)
            if len(batched) > 1:
                MONITOR.inc("brain_tool_calls_batched_total")
                tool_call = batched[0]
    except Exception as e:
        logger.debug("resolve_tool_calls_from_first_content: %s", e, exc_info=True)
    return tool_call, batched


async def _try_first_stage_stream(
    *,
    prompt: str,
    sys_first: str,
    first_max_tok: int,
    temp_first: float,
    first_stage_vision: Any,
    telemetry_extra: Dict[str, Any],
    llm_session_id: str,
    kv_cache_tail: Any,
) -> Optional[Dict[str, Any]]:
    """Admin lab: brain_first через SSE + CoT в progress-сообщении."""
    try:
        from core.telegram_stream_reply import (
            TelegramStreamEditor,
            get_chat_cancel_event,
            run_streaming_llm_to_telegram,
            telegram_stream_get_bound,
            telegram_stream_reply_enabled,
        )
        from core.telegram_stream_reasoning import stream_reasoning_armed
    except ImportError:
        return None

    if not telegram_stream_reply_enabled() or not stream_reasoning_armed():
        return None
    bound = telegram_stream_get_bound()
    if not bound:
        return None
    if first_stage_vision:
        return None

    bot, chat_id, mid, _uid = bound
    cancel_ev = get_chat_cancel_event(str(chat_id))
    if cancel_ev is None:
        cancel_ev = asyncio.Event()
    editor = TelegramStreamEditor(bot, chat_id, mid, show_reasoning=True)
    await editor.finalize("🧠 Думаю…", remove_stop=False)
    gen_kw: Dict[str, Any] = {
        "prompt": prompt,
        "system_prompt": sys_first,
        "max_tokens": first_max_tok,
        "temperature": temp_first,
        "telemetry_tag": "brain_first",
        "telemetry_extra": telemetry_extra,
        "session_id": llm_session_id,
        "conversation_id": llm_session_id,
        "kv_cache_tail": kv_cache_tail,
    }
    try:
        timeout = float((os.getenv("BRAIN_LLM_FREE_TIMEOUT_SEC") or "55").strip() or "55")
    except ValueError:
        timeout = 55.0
    timeout = max(15.0, min(timeout, 240.0))
    try:
        return await with_timeout(
            run_streaming_llm_to_telegram(
                llm=_llm,
                gen_kw=gen_kw,
                cancel_event=cancel_ev,
                editor=editor,
            ),
            timeout_sec=timeout,
            tag="llm_first_stage_stream",
        )
    except Exception as e:
        logger.warning("[brain] first stage stream failed: %s", e)
        return {"error": str(e), "content": ""}


async def run_first_stage_llm(
    *,
    user_id: str,
    prompt: str,
    sys_first: str,
    first_max_tok: int,
    temp_first: float,
    first_stage_vision: Any,
    tier_first_timeout: Optional[float],
    task_tier: str,
    telemetry_extra: Dict[str, Any],
    llm_session_id: str,
    kv_cache_tail: Any,
    brain_profile: str,
    prof_primary: Any,
    allowed_tool_names: Set[str],
    context: Dict[str, Any],
) -> FirstStageOutcome:
    """Цикл first LLM + retry невалидного TOOL_CALL."""
    max_tc_retry = max_tool_call_retries()
    correction = ""
    first: Dict[str, Any] = {}
    first_content = ""
    tc_attempt = 0
    group_id = context.get("group_id") if isinstance(context, dict) else None

    while True:
        prompt_use = prompt + correction
        try:
            if tc_attempt == 0 and not correction:
                streamed = await _try_first_stage_stream(
                    prompt=prompt_use,
                    sys_first=sys_first,
                    first_max_tok=first_max_tok,
                    temp_first=temp_first,
                    first_stage_vision=first_stage_vision,
                    telemetry_extra=telemetry_extra,
                    llm_session_id=llm_session_id,
                    kv_cache_tail=kv_cache_tail,
                )
                if streamed is not None:
                    first = streamed
                elif env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
                    first = await llm_generate_tiered(
                        _llm,
                        tag="llm_first_stage",
                        prompt=prompt_use,
                        system_prompt=sys_first,
                        max_tokens=first_max_tok,
                        temperature=temp_first,
                        vision_image_parts=first_stage_vision,
                        base_timeout=tier_first_timeout,
                        task_tier=task_tier,
                        telemetry_tag="brain_first",
                        telemetry_extra=telemetry_extra,
                        session_id=llm_session_id,
                        conversation_id=llm_session_id,
                        kv_cache_tail=kv_cache_tail,
                    )
                else:
                    first = await with_timeout(
                        _llm.generate(
                            prompt=prompt_use,
                            system_prompt=sys_first,
                            max_tokens=first_max_tok,
                            temperature=temp_first,
                            vision_image_parts=first_stage_vision,
                            telemetry_tag="brain_first",
                            telemetry_extra=telemetry_extra,
                            session_id=llm_session_id,
                            conversation_id=llm_session_id,
                            kv_cache_tail=kv_cache_tail,
                        ),
                        timeout_sec=float(tier_first_timeout)
                        if tier_first_timeout
                        else float(DEFAULT_TIMEOUT_SEC),
                        tag="llm_first_stage",
                    )
            elif env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
                first = await llm_generate_tiered(
                    _llm,
                    tag="llm_first_stage",
                    prompt=prompt_use,
                    system_prompt=sys_first,
                    max_tokens=first_max_tok,
                    temperature=temp_first,
                    vision_image_parts=first_stage_vision,
                    base_timeout=tier_first_timeout,
                    task_tier=task_tier,
                    telemetry_tag="brain_first",
                    telemetry_extra=telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                    kv_cache_tail=kv_cache_tail,
                )
            else:
                first = await with_timeout(
                    _llm.generate(
                        prompt=prompt_use,
                        system_prompt=sys_first,
                        max_tokens=first_max_tok,
                        temperature=temp_first,
                        vision_image_parts=first_stage_vision,
                        telemetry_tag="brain_first",
                        telemetry_extra=telemetry_extra,
                        session_id=llm_session_id,
                        conversation_id=llm_session_id,
                        kv_cache_tail=kv_cache_tail,
                    ),
                    timeout_sec=float(tier_first_timeout)
                    if tier_first_timeout
                    else float(DEFAULT_TIMEOUT_SEC),
                    tag="llm_first_stage",
                )
        except Exception as e:
            err_msg = (str(e).strip() or type(e).__name__ or "unknown_error")
            logger.error(
                "[brain] first llm call failed: %s",
                err_msg,
                exc_info=not isinstance(e, asyncio.TimeoutError),
            )
            record_error_event("brain", "first_llm_generate", exc=e, extra={"user_id": user_id})
            first = {"error": err_msg, "content": ""}
            break

        if first.get("error"):
            break

        try:
            _increment_kv_turn(user_id=user_id, group_id=group_id)
        except Exception as e:
            logger.debug("first_stage kv_turn: %s", e, exc_info=True)

        if first.get("cached"):
            telemetry_extra["tokens_cached"] = int(telemetry_extra.get("tokens_cached") or 0) + len(
                prompt_use
            ) // 4
            MONITOR.inc("brain_prompt_cache_hit_total")

        usage_detail = first.get("usage_detail") or {}
        try:
            _record_kv_trace(
                {
                    "event": "llm_first_response",
                    "user_id": user_id,
                    "session_id": llm_session_id,
                    "ok": not bool(first.get("error")),
                    "latency_ms": first.get("latency_ms", 0),
                    "usage": {
                        "prompt": usage_detail.get("prompt_tokens"),
                        "completion": usage_detail.get("completion_tokens"),
                        "cached": usage_detail.get("cached_prompt_tokens"),
                        "cache_write": usage_detail.get("cache_write_tokens"),
                        "reasoning": usage_detail.get("reasoning_tokens"),
                    },
                    "model": first.get("model"),
                    "upstream": first.get("upstream_model"),
                }
            )
        except Exception as e:
            logger.debug("first_stage kv_trace: %s", e, exc_info=True)

        first_content = normalize_first_stage_content(
            first.get("content", ""),
            brain_profile=brain_profile,
            prof_primary=prof_primary,
        )
        if _looks_like_repetition_glitch(first_content):
            logger.warning(
                "[brain] first stage glitchy completion discarded (model=%s)",
                first.get("model"),
            )
            first_content = ""
            break

        if "TOOL_CALL:" not in first_content:
            break
        issue = describe_tool_call_retry_issue(first_content, allowed_tool_names)
        if not issue:
            break
        if tc_attempt >= max_tc_retry:
            logger.warning("[brain] invalid or truncated TOOL_CALL after retries: %s", issue)
            MONITOR.inc("brain_tool_call_invalid_total")
            first_content = text_before_tool_call(first_content)
            break
        correction = (
            f"\n\n[исправление] {issue} Ответь ещё раз: текст без TOOL_CALL "
            "или один валидный TOOL_CALL; name только из списка tools выше."
        )
        MONITOR.inc("brain_tool_call_retry_total")
        tc_attempt += 1

    return FirstStageOutcome(
        first=first,
        first_content=first_content,
        has_llm_error=bool(first.get("error")),
    )
