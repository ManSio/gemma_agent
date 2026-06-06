"""Один вызов LLM без tools — для простых explain/general (фаза 3 PRODUCT_FINISH)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.brain.cot_strip import strip_leaked_cot
from core.brain.runtime import _llm, _memory, _persona
from core.brain.text_helpers import (
    TELEGRAM_PLAIN_REPLY_RULE,
    natural_fallback_response,
    safe_text,
    strip_chat_markdown_for_telegram,
)
from core.error_analysis import record_error_event
from core.model_profile import ModelProfile, clamp_temperature, merge_system, resolve_model_profile
from core.monitoring import MONITOR
from core.resilience import DEFAULT_TIMEOUT_SEC, with_timeout
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_LEAK_LINE = re.compile(
    r"(?i)(tool_call|available tools|args_schema|системный блок|admin_connectivity)"
)


def _direct_dialog_max_tokens() -> int:
    try:
        v = int((os.getenv("BRAIN_DIRECT_DIALOG_MAX_TOKENS") or "512").strip())
    except ValueError:
        v = 512
    return max(128, min(v, 1536))


def _direct_dialog_model_id(llm: Any) -> Optional[str]:
    override = (os.getenv("BRAIN_DIRECT_DIALOG_MODEL") or "").strip()
    if override:
        return override
    try:
        from core.brain.chat_agent_mode import use_premium_for_direct

        if use_premium_for_direct():
            main = (os.getenv("OPENROUTER_MODEL") or "").strip()
            if main:
                return main
    except Exception as e:
        logger.debug('%s optional failed: %s', 'direct_dialog_reply', e, exc_info=True)
    if not effective_bool("BRAIN_DIRECT_DIALOG_FORCE_FREE", default=True):
        return None
    return (os.getenv("OPENROUTER_MODEL_FREE") or "").strip() or str(
        getattr(llm, "free_model", None) or "openrouter/free"
    ).strip()


def _trim_reply(text: str) -> str:
    lines = [ln.strip() for ln in safe_text(text).splitlines() if ln.strip()]
    good = [ln for ln in lines if not _LEAK_LINE.search(ln)]
    body = "\n".join(good).strip() if good else safe_text(text).strip()
    return body[:3500]


async def _direct_dialog_llm_once(
    *,
    gen_kw: Dict[str, Any],
    stream_bound: Any,
    timeout_sec: float,
    tag: str = "llm_direct_dialog",
) -> Dict[str, Any]:
    if stream_bound:
        from core.telegram_stream_reply import (
            TelegramStreamEditor,
            get_chat_cancel_event,
            run_streaming_llm_to_telegram,
        )

        bot, chat_id, mid, _uid = stream_bound
        cancel_ev = get_chat_cancel_event(str(chat_id))
        if cancel_ev is None:
            cancel_ev = asyncio.Event()
        from core.telegram_stream_reasoning import stream_reasoning_armed

        editor = TelegramStreamEditor(
            bot,
            chat_id,
            mid,
            show_reasoning=stream_reasoning_armed(),
        )
        if stream_reasoning_armed():
            await editor.finalize("🧠 Думаю…", remove_stop=False)
        else:
            await editor.finalize("…", remove_stop=False)
        return await with_timeout(
            run_streaming_llm_to_telegram(
                llm=_llm,
                gen_kw=gen_kw,
                cancel_event=cancel_ev,
                editor=editor,
            ),
            timeout_sec=timeout_sec,
            tag=tag if "_stream" in tag else f"{tag}_stream",
        )
    return await with_timeout(
        _llm.generate(**gen_kw),
        timeout_sec=timeout_sec,
        tag=tag,
    )


async def brain_direct_dialog_reply(
    *,
    user_text: str,
    user_id: str,
    system_prompt: str,
    persona: Dict[str, Any],
    memory_facts: List[Any],
    recent_dialogue: List[Any],
    skip_memory_writes: bool,
    model_profile: ModelProfile,
    llm_session_id: str = "",
    external_hint: str = "",
    context: Optional[Dict[str, Any]] = None,
    brain_profile: str = "",
) -> str:
    from core.telegram_stream_reply import telegram_stream_get_bound, telegram_stream_reply_enabled

    _stream_bound = telegram_stream_get_bound() if telegram_stream_reply_enabled() else None
    if not _stream_bound:
        try:
            from core.telegram_progress import telegram_progress_pulse

            await telegram_progress_pulse("💬 Отвечаю…", force=True)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'direct_dialog_reply', e, exc_info=True)
    try:
        from core.brain.chat_agent_mode import direct_dialog_recent_turns

        _recent_n = direct_dialog_recent_turns()
    except Exception:
        _recent_n = 6
    mf = memory_facts[:12] if isinstance(memory_facts, list) else []
    recent = (
        recent_dialogue[-_recent_n:]
        if isinstance(recent_dialogue, list)
        else []
    )
    fm = _direct_dialog_model_id(_llm)
    prof = resolve_model_profile(fm) if fm else model_profile
    sys_line = merge_system(system_prompt, prof.system_addon_first)
    hint_block = (external_hint or "").strip()[:2000]
    prompt = f"""
Системная роль (кратко): {sys_line}

{TELEGRAM_PLAIN_REPLY_RULE}

Ответь по последней реплике пользователя: связно, по сути, без вызова инструментов и без TOOL_CALL.
Не пиши рассуждения «пользователь спрашивает…». Если не уверен в факте — скажи честно.

Персона (фрагмент): {persona}
Память: {mf}
Недавний диалог: {recent}
{f"Подсказки оператора:{chr(10)}{hint_block}" if hint_block else ""}

Вопрос пользователя:
{user_text}
"""
    lane_sys = merge_system(
        "Ты отвечаешь пользователю в Telegram одним сообщением. Без инструментов. "
        "Первые слова — сразу ответ, не мета-комментарий.",
        prof.system_addon_first,
    )
    try:
        from core.brain.context_budget import maybe_log_context_budget_warning

        maybe_log_context_budget_warning(tag="brain_direct_dialog", prompt=prompt, system_prompt=lane_sys)
        from core.brain.brain_telemetry import make_brain_telemetry_extra

        bp = (brain_profile or "").strip() or "standard"
        _tel_extra = make_brain_telemetry_extra(
            bp,
            prompt_tokens_est=max(1, len(prompt) // 4),
            prompt_chars=len(prompt),
        )
        gen_kw: Dict[str, Any] = {
            "prompt": prompt,
            "system_prompt": lane_sys,
            "max_tokens": _direct_dialog_max_tokens(),
            "temperature": clamp_temperature(0.45, prof.temperature_first_delta),
            "telemetry_tag": "brain_direct_dialog",
            "telemetry_extra": _tel_extra,
            "session_id": llm_session_id,
            "conversation_id": llm_session_id,
        }
        if fm:
            gen_kw["model"] = fm
        out = await _direct_dialog_llm_once(
            gen_kw=gen_kw,
            stream_bound=_stream_bound,
            timeout_sec=min(45.0, float(DEFAULT_TIMEOUT_SEC)),
        )
    except Exception as e:
        logger.warning("[brain] direct_dialog llm failed: %s", e)
        record_error_event("brain", "direct_dialog_llm", exc=e, extra={"user_id": user_id})
        out = {"error": str(e), "content": ""}

    if out.get("error"):
        from core.brain.llm_transient_recovery import (
            is_transient_llm_error,
            retry_openrouter_generate,
        )

        if is_transient_llm_error(str(out.get("error") or "")):
            if _stream_bound:
                out = await _direct_dialog_llm_once(
                    gen_kw=gen_kw,
                    stream_bound=_stream_bound,
                    timeout_sec=min(45.0, float(DEFAULT_TIMEOUT_SEC)),
                    tag="llm_direct_dialog_stream_retry",
                )
            else:
                out = await retry_openrouter_generate(
                    _llm,
                    gen_kw,
                    timeout_sec=min(45.0, float(DEFAULT_TIMEOUT_SEC)),
                    tag="llm_direct_dialog",
                )
    if out.get("error"):
        reply = natural_fallback_response("llm_error", user_id, user_text)
    else:
        reply = strip_leaked_cot(
            safe_text(out.get("content", "")),
            extra_markers_en=prof.cot_extra_markers_en,
            extra_markers_ru=prof.cot_extra_markers_ru,
        )
        reply = _trim_reply(reply)
        if not reply.strip():
            reply = natural_fallback_response("empty_llm", user_id, user_text)

    try:
        if effective_bool("BRAIN_STRIP_CHAT_MARKDOWN", default=True):
            reply = strip_chat_markdown_for_telegram(reply)
        reply = _persona.apply_persona_to_response(user_id, reply)
        if not skip_memory_writes:
            await _memory.on_after_response(user_id, reply)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'direct_dialog_reply', e, exc_info=True)
    if not (reply or "").strip():
        reply = natural_fallback_response("empty_llm", user_id, user_text)
    if isinstance(context, dict):
        try:
            from core.brain.brain_telemetry import (
                make_brain_telemetry_extra,
                prompt_tokens_est_from_usage,
                stash_brain_turn_telemetry,
            )

            bp = (brain_profile or "").strip() or "standard"
            usage = out.get("usage") if isinstance(out.get("usage"), dict) else {}
            pt_est = prompt_tokens_est_from_usage(usage, prompt=prompt)
            tel = make_brain_telemetry_extra(bp, prompt_tokens_est=pt_est, prompt_chars=len(prompt))
            stash_brain_turn_telemetry(
                context,
                telemetry_extra=tel,
                brain_profile=bp,
                brain_recent_limit=int(tel.get("brain_recent_limit") or 0),
            )
        except Exception as e:
            logger.debug("direct_dialog telemetry stash: %s", e)
    MONITOR.inc("brain_direct_dialog_total")
    return reply
