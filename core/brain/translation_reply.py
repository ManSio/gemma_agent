"""Короткий LLM-вызов только для перевода — без tools, skills и полного prompt pack."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.brain.response_finalize import finalize_user_reply
from core.brain.runtime import _llm, _memory
from core.brain.text_helpers import natural_fallback_response, safe_text, strip_chat_markdown_for_telegram
from core.brain.translation_path import parse_translation_request, parse_translation_requests
from core.error_analysis import record_error_event
from core.model_profile import ModelProfile, clamp_temperature, merge_system, resolve_model_profile
from core.monitoring import MONITOR
from core.resilience import DEFAULT_TIMEOUT_SEC, with_timeout

logger = logging.getLogger(__name__)

_LANG_LABEL = {
    "en": "English",
    "ru": "Russian",
    "be": "Belarusian",
    "uk": "Ukrainian",
    "de": "German",
    "fr": "French",
}

_LEAK_LINE = re.compile(
    r"(?i)"
    r"(available tools|системное сообщение|tool_names|tools_full_index|"
    r"tool_call|selected_skill|memory_facts|recent_dialogue|"
    r"примечание:.*перевод|не вызывай.*tool|"
    r"admin,|aduPadruchnik|universalsearch)"
)

_TOOL_CATALOG_LINE = re.compile(
    r"(?i)^\s*(admin|arithmetic|universalsearch|urlfetch|wikipedia|selfprogramming)"
    r"[\s,]"
)


def _translation_max_tokens() -> int:
    try:
        v = int((os.getenv("BRAIN_TRANSLATION_MAX_TOKENS") or "256").strip())
    except ValueError:
        v = 256
    return max(64, min(v, 1024))


def _translation_model_id(llm: Any) -> Optional[str]:
    override = (os.getenv("BRAIN_TRANSLATION_MODEL") or "").strip()
    if override:
        return override
    raw = os.getenv("BRAIN_TRANSLATION_FORCE_FREE", "true").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return None
    return (os.getenv("OPENROUTER_MODEL_FREE") or "").strip() or str(
        getattr(llm, "free_model", None) or "openrouter/free"
    ).strip()


def _pick_best_line(text: str, *, target_lang: Optional[str]) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    good: list[str] = []
    for ln in lines:
        if _LEAK_LINE.search(ln) or _TOOL_CATALOG_LINE.search(ln):
            continue
        if len(ln) > 400:
            continue
        good.append(ln)
    if not good:
        return ""
    if target_lang == "en":
        latin = [ln for ln in good if sum(c.isascii() and c.isalpha() for c in ln) >= max(3, len(ln) // 3)]
        if latin:
            return latin[-1]
    if target_lang == "ru":
        cyr = [ln for ln in good if re.search(r"[а-яё]", ln, re.I)]
        if cyr:
            return cyr[-1]
    return good[-1]


def sanitize_translation_reply(
    text: str,
    *,
    user_text: str = "",
    target_lang: Optional[str] = None,
    source_fragment: Optional[str] = None,
) -> str:
    s = finalize_user_reply(text, user_text=user_text)
    s = _pick_best_line(s, target_lang=target_lang)
    s = (s or "").strip().strip('"').strip("'")
    if source_fragment and s.lower() == source_fragment.strip().lower() and target_lang == "en":
        return ""
    return s


async def _translate_one_piece(
    *,
    piece: str,
    tgt: Optional[str],
    user_text: str,
    user_id: str,
    model_profile: ModelProfile,
    llm_session_id: str,
) -> str:
    lang_label = _LANG_LABEL.get(tgt or "", tgt or "the requested language")
    from core.brain.directive_blocks import compose_system_prompt

    fm = _translation_model_id(_llm)
    prof = resolve_model_profile(fm) if fm else model_profile
    sys_prompt = merge_system(
        compose_system_prompt("translation"),
        "Output ONLY the translated text. No preamble, no tool lists, no meta notes.",
        prof.system_addon_first,
    )
    user_block = f"Translate into {lang_label}:\n{piece}"
    try:
        gen_kw: Dict[str, Any] = {
            "prompt": user_block,
            "system_prompt": sys_prompt,
            "max_tokens": _translation_max_tokens(),
            "temperature": clamp_temperature(0.15, prof.temperature_first_delta),
            "telemetry_tag": "brain_translation",
            "session_id": llm_session_id,
            "conversation_id": llm_session_id,
        }
        if fm:
            gen_kw["model"] = fm
        out = await with_timeout(
            _llm.generate(**gen_kw),
            timeout_sec=min(35.0, float(DEFAULT_TIMEOUT_SEC)),
            tag="llm_translation",
        )
    except Exception as e:
        logger.warning("[brain] translation llm failed: %s", e)
        record_error_event("brain", "translation_llm", exc=e, extra={"user_id": user_id})
        return natural_fallback_response("llm_error", user_id, user_text)

    if out.get("error"):
        return natural_fallback_response("llm_error", user_id, user_text)

    raw = safe_text(out.get("content", ""))
    reply = sanitize_translation_reply(
        raw,
        user_text=user_text,
        target_lang=tgt,
        source_fragment=piece,
    )
    if not reply.strip():
        reply = sanitize_translation_reply(
            raw.split("TOOL_CALL:")[0],
            user_text=user_text,
            target_lang=tgt,
            source_fragment=piece,
        )
    if not reply.strip() and tgt == "en" and piece:
        _simple = {
            "привет, как дела": "Hello, how are you?",
            "привет": "Hello",
            "как дела": "How are you?",
            "спокойной ночи": "Good night",
        }
        key = piece.lower().strip(" '\"")
        reply = _simple.get(key, "")
    return reply.strip()


async def brain_translation_reply(
    *,
    user_text: str,
    user_id: str,
    skip_memory_writes: bool,
    model_profile: ModelProfile,
    llm_session_id: str = "",
) -> str:
    requests = parse_translation_requests(user_text)
    if not requests:
        return "Укажите текст для перевода в кавычках или после двоеточия."

    try:
        from core.telegram_progress import telegram_progress_pulse

        await telegram_progress_pulse("🌐 Перевод…", force=True)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'translation_reply', e, exc_info=True)
    lines: List[str] = []
    for tgt, frag in requests:
        piece = (frag or "").strip()
        if not piece:
            continue
        one = await _translate_one_piece(
            piece=piece,
            tgt=tgt,
            user_text=user_text,
            user_id=user_id,
            model_profile=model_profile,
            llm_session_id=llm_session_id,
        )
        if one.strip():
            lines.append(one.strip())

    reply = "\n".join(lines)
    if not reply.strip():
        return natural_fallback_response("empty_llm", user_id, user_text)

    try:
        if os.getenv("BRAIN_STRIP_CHAT_MARKDOWN", "true").strip().lower() in {"1", "true", "yes", "on"}:
            reply = strip_chat_markdown_for_telegram(reply)
        if not skip_memory_writes:
            await _memory.on_after_response(user_id, reply)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'translation_reply', e, exc_info=True)
    MONITOR.inc("brain_translation_fast_path_total")
    return reply.strip()
