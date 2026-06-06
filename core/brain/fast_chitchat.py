"""Короткий диалог без инструментов."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.error_analysis import record_error_event
from core.monitoring import MONITOR
from core.model_profile import ModelProfile, clamp_temperature, merge_system, resolve_model_profile
from core.resilience import DEFAULT_TIMEOUT_SEC, with_timeout

from core.brain.cot_strip import strip_leaked_cot
from core.brain.runtime import _llm, _memory, _persona
from core.brain.env import env_flag
from core.brain.text_helpers import (
    TELEGRAM_PLAIN_REPLY_RULE,
    natural_fallback_response,
    safe_text,
    strip_chat_markdown_for_telegram,
)

logger = logging.getLogger(__name__)


def _fast_chitchat_max_tokens() -> int:
    """DeepSeek и др. могут отъедать лимит reasoning-токенами — оставляем запас на видимый ответ."""
    try:
        v = int((os.getenv("BRAIN_FAST_CHITCHAT_MAX_TOKENS") or "168").strip())
    except ValueError:
        v = 168
    return max(96, min(v, 512))


def _fast_chitchat_model_id(llm: Any) -> Optional[str]:
    """
    Короткий чат — отдельно от tiered «первой ступени»: берём именно OpenRouter free-маршрут
    (`OPENROUTER_MODEL_FREE`, по умолчанию openrouter/free), а не `BRAIN_LLM_FREE_MODEL`
    (там может стоять другая «бесплатная» модель вроде deepseek flash).
    Иначе OpenRouterProvider без явного `model` мог переключить _get_current_model() на qwen/dev.
    """
    if not env_flag("BRAIN_FAST_CHITCHAT_FORCE_FREE_MODEL", default=True):
        return None
    override = (os.getenv("BRAIN_FAST_CHITCHAT_MODEL") or "").strip()
    if override:
        return override
    env_free = (os.getenv("OPENROUTER_MODEL_FREE") or "").strip()
    if env_free:
        return env_free
    return str(getattr(llm, "free_model", None) or "openrouter/free").strip()


_CHITCHAT_META_PREFIXES = (
    "мысленно",
    "мы сейчас ",
    "пользователь ",
    "пользователь написал",
    "пользователь спрашивает",
    "пользователь пишет",
    "пользователь сказал",
    "контекст показывает",
    "контекст: ",
    "по контексту",
    "возможно, это",
    "возможно это",
    "теперь пользователь",
    "ранее ",
    "это простое",
    "это уже ",
    "мне нужно ответить",
    "надо ответить",
    "нужно ответить",
    "я должен ответить",
)

# Короткие строки-планирование (часто без префикса «пользователь …»).
_CHITCHAT_META_SNIPPETS = (
    "перебираю детали",
    "детали запроса",
    "по контексту видно",
    "ранее ассистент",
    "ассистент ответил",
    "это простое приветствие",
    "это уже второй",
    "возможно, это тест",
    "нужно ответить кратко",
    "нужно ответить тепло",
    "кратко, тепло",
)


def _sanitize_chitchat_reply(text: str) -> str:
    """Срезает типичный мета-пролог и оставляет пользовательский ответ."""
    body = safe_text(text)
    if not body:
        return ""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return ""
    out: List[str] = []
    skipping = True
    for ln in lines:
        low = ln.lower()
        if skipping:
            if any(low.startswith(p) for p in _CHITCHAT_META_PREFIXES):
                continue
            if len(ln) < 260 and any(s in low for s in _CHITCHAT_META_SNIPPETS):
                continue
        skipping = False
        out.append(ln)
    cleaned = "\n".join(out).strip()
    if not cleaned:
        return ""
    # Весь ответ — один сплошной мета-блок без «чистой» реплики: последний абзац тоже план.
    blob = cleaned.lower()
    if len(cleaned) > 180 and any(s in blob for s in _CHITCHAT_META_SNIPPETS + ("мысленно", "по контексту видно")):
        parts = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
        for p in reversed(parts):
            pl = p.lower()
            if len(p) > 320:
                continue
            if any(x in pl for x in _CHITCHAT_META_SNIPPETS):
                continue
            if any(pl.startswith(pref) for pref in _CHITCHAT_META_PREFIXES):
                continue
            if "мысленно" in pl or "по контексту видно" in pl:
                continue
            return p
        return ""
    # На случай, если модель выдала JSON/разметку рассуждений вместо ответа.
    if re.match(r"^\s*[\{\[]", cleaned):
        return ""
    return cleaned


def deterministic_pure_chitchat_reply(user_text: str, user_id: str) -> str:
    """Мгновенный ответ без LLM на чистый читчат (привет / как дела)."""
    from core.prompt_routing import is_pure_chitchat_private

    if not is_pure_chitchat_private(user_text):
        return ""
    ut = (user_text or "").strip().lower()
    if any(x in ut for x in ("как дела", "как жизнь", "как ты", "как настроение", "что делаешь")):
        opts = (
            "Нормально, спасибо! А у тебя как?",
            "Всё хорошо, на связи. Как сам?",
            "Отлично, спасибо что спросил! Чем помочь?",
        )
    elif ut.startswith(("спасибо", "благодар")):
        opts = ("Пожалуйста!", "Рад помочь!", "Обращайся!")
    else:
        from core.brain.text_helpers import natural_fallback_response

        return natural_fallback_response("empty_llm", user_id, user_text)
    idx = int(hashlib.sha256(f"{user_id}:{ut[:32]}".encode()).hexdigest(), 16)
    return opts[idx % len(opts)]


async def brain_fast_chitchat_reply(
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
    context: Optional[Dict[str, Any]] = None,
    brain_profile: str = "chitchat",
) -> str:
    """Один короткий вызов LLM без инструментов и skills — как лёгкий диалог в GENESIS."""
    if env_flag("BRAIN_FAST_CHITCHAT_DETERMINISTIC", default=True):
        det = deterministic_pure_chitchat_reply(user_text, user_id)
        if (det or "").strip():
            out = strip_chat_markdown_for_telegram(det.strip())
            try:
                if not skip_memory_writes:
                    await _memory.on_after_response(user_id, out)
            except Exception as e:
                logger.debug("fast_chitchat deterministic persist: %s", e)
            MONITOR.inc("brain_fast_chitchat_total")
            MONITOR.inc("brain_fast_chitchat_deterministic_total")
            return out
    try:
        from core.telegram_progress import telegram_progress_pulse

        await telegram_progress_pulse("⚡ Короткий ответ…", force=True)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'fast_chitchat', e, exc_info=True)
    mf = memory_facts[:5] if isinstance(memory_facts, list) else []
    recent = recent_dialogue[-4:] if isinstance(recent_dialogue, list) else []
    fm = _fast_chitchat_model_id(_llm)
    prof = resolve_model_profile(fm) if fm else model_profile
    sys_line = merge_system(system_prompt, prof.system_addon_first)
    prompt = f"""
Системная роль (кратко): {sys_line}

Ответь на реплику очень кратко (1–2 коротких предложения), тепло и по-человечески.
Строго запрещено писать внутренние рассуждения/анализ в стиле «пользователь спрашивает», «мне нужно ответить», «контекст показывает».
Не выдумывай slash-команды (например /llm_telemetry — такой команды нет; для токенов у админа есть /admin_llm_usage).
Не раздувай ответ, не давай лекций и общих советов «про жизнь», не выдумывай факты о человеке.
Если в памяти есть имя или явный факт — можно учесть ненавязчиво.

Стиль/персона (фрагмент): {persona}
Фрагменты памяти: {mf}
Недавние реплики (контекст): {recent}

Сообщение пользователя:
{user_text}
"""
    chitchat_sys = merge_system(
        "Отвечай только обычным текстом пользователю. Без инструментов и TOOL_CALL. "
        "Первое слово ответа — сразу приветствие или ответ пользователю; запрещены разборы "
        "в духе «мысленно перебираю», «по контексту видно», «пользователь сказал», «нужно ответить».",
        prof.system_addon_first,
    )
    bp = (brain_profile or "").strip() or "chitchat"
    try:
        from core.brain.brain_telemetry import make_brain_telemetry_extra

        _tel_extra = make_brain_telemetry_extra(
            bp,
            prompt_tokens_est=max(1, len(prompt) // 4),
            prompt_chars=len(prompt),
        )
        gen_kw: Dict[str, Any] = {
            "prompt": prompt,
            "system_prompt": chitchat_sys,
            "max_tokens": _fast_chitchat_max_tokens(),
            "temperature": clamp_temperature(0.35, prof.temperature_first_delta),
            "telemetry_tag": "brain_fast_chitchat",
            "telemetry_extra": _tel_extra,
            "session_id": llm_session_id,
            "conversation_id": llm_session_id,
        }
        if fm:
            gen_kw["model"] = fm
        out = await with_timeout(
            _llm.generate(**gen_kw),
            timeout_sec=min(25.0, float(DEFAULT_TIMEOUT_SEC)),
            tag="llm_fast_chitchat",
        )
    except Exception as e:
        err_msg = (str(e).strip() or type(e).__name__ or "unknown_error")
        logger.warning("[brain] fast chitchat llm failed: %s", err_msg)
        record_error_event("brain", "fast_chitchat_llm", exc=e, extra={"user_id": user_id})
        out = {"error": err_msg, "content": ""}

    if out.get("error"):
        from core.brain.llm_transient_recovery import (
            is_transient_llm_error,
            retry_openrouter_generate,
        )

        if is_transient_llm_error(str(out.get("error") or "")):
            out = await retry_openrouter_generate(
                _llm,
                gen_kw,
                timeout_sec=min(25.0, float(DEFAULT_TIMEOUT_SEC)),
                tag="llm_fast_chitchat",
            )
    if out.get("error"):
        reply = natural_fallback_response("llm_error", user_id, user_text)
        try:
            if not skip_memory_writes:
                await _memory.on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'fast_chitchat', e, exc_info=True)
        return reply

    reply = strip_leaked_cot(
        safe_text(out.get("content", "")),
        extra_markers_en=prof.cot_extra_markers_en,
        extra_markers_ru=prof.cot_extra_markers_ru,
    )
    reply = _sanitize_chitchat_reply(reply)
    if not reply.strip():
        reply = natural_fallback_response("empty_llm", user_id, user_text)
    try:
        if env_flag("BRAIN_STRIP_CHAT_MARKDOWN", default=True):
            reply = strip_chat_markdown_for_telegram(reply)
        reply = _persona.apply_persona_to_response(user_id, reply)
        if not (reply or "").strip():
            reply = natural_fallback_response("empty_llm", user_id, user_text)
        if not skip_memory_writes:
            await _memory.on_after_response(user_id, reply)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'fast_chitchat', e, exc_info=True)
    if not (reply or "").strip():
        reply = natural_fallback_response("empty_llm", user_id, user_text)
    if isinstance(context, dict):
        try:
            from core.brain.brain_telemetry import (
                make_brain_telemetry_extra,
                prompt_tokens_est_from_usage,
                stash_brain_turn_telemetry,
            )

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
            logger.debug("fast_chitchat telemetry stash: %s", e)
    MONITOR.inc("brain_fast_chitchat_total")
    return reply
