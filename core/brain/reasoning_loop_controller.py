"""
Единый контур «глубокого» текстового ответа внутри одной реплики пользователя.

Не заменяет Goal Runner и не дублирует цепочку tool→second stage.
Срабатывает только когда первый проход мозга **уже дал текст без TOOL_CALL**.

Вкл: BRAIN_REASONING_LOOP_ENABLED=true
Режим: BRAIN_REASONING_LOOP_MODE=tier (только nested/deep) | always (ещё и shallow при длине)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict

from core.llm_tiered import llm_generate_tiered
from core.monitoring import MONITOR
from core.resilience import DEFAULT_TIMEOUT_SEC, with_timeout

from core.brain.cot_strip import strip_leaked_cot
from core.brain.env import env_flag
from core.brain.runtime import _llm
from core.brain.text_helpers import TELEGRAM_PLAIN_REPLY_RULE, looks_like_repetition_glitch, safe_text
from core.model_profile import clamp_temperature, merge_system
from core.task_depth import tier_prefers_thorough

logger = logging.getLogger(__name__)

# Паттерны, указывающие на то, что пользователь исправляет бота
_CORRECTION_PATTERNS = (
    r"(?i)\b(нет|не так|не то|неправ|не верно|неверно|ошибся|ошибк|исправь|поправь|"
    r"ты не понял|ты не прав|ты ошибаешься|не правильно|некорректно|"
    r"я другое имел|я не то имел|я про другое|я о другом|"
    r"переделай|перепиши|заново|сначала|иначе|по другому|по-другому|"
    r"задача не решена|не решена|неправильно|всё не так|все не так)\b"
)


def user_correcting_bot(user_text: str) -> bool:
    """True если пользователь явно исправляет/поправляет бота."""
    raw = (user_text or "").strip()
    if not raw:
        return False
    if re.search(_CORRECTION_PATTERNS, raw):
        return True
    low = raw.lower()
    # Короткие энергичные несогласия
    if low in {"нет", "не", "no", "nope"} and len(low) <= 5:
        return True
    return False


def reasoning_loop_enabled() -> bool:
    return env_flag("BRAIN_REASONING_LOOP_ENABLED", default=True)


def _loop_mode() -> str:
    m = (os.getenv("BRAIN_REASONING_LOOP_MODE") or "tier").strip().lower()
    return m if m in {"tier", "always"} else "tier"


def _min_user_chars() -> int:
    try:
        return max(40, int((os.getenv("BRAIN_REASONING_LOOP_MIN_USER_CHARS") or "96").strip()))
    except ValueError:
        return 96


def _critique_max_tokens() -> int:
    try:
        return max(200, min(900, int((os.getenv("BRAIN_REASONING_LOOP_CRITIQUE_MAX_TOKENS") or "520").strip())))
    except ValueError:
        return 520


def _final_max_tokens() -> int:
    try:
        return max(400, min(4000, int((os.getenv("BRAIN_REASONING_LOOP_FINAL_MAX_TOKENS") or "1400").strip())))
    except ValueError:
        return 1400


def wants_reasoning_loop(user_text: str, context: Dict[str, Any], task_tier: str) -> bool:
    if not reasoning_loop_enabled():
        return False
    ctx = context if isinstance(context, dict) else {}
    if ctx.get("brain_disable_reasoning_loop"):
        return False
    if ctx.get("brain_force_reasoning_loop"):
        return True
    raw = (user_text or "").strip()
    # Пользователь исправляет — форсируем loop даже для коротких сообщений
    if user_correcting_bot(raw):
        return True
    ds = ctx.get("dialogue_state") if isinstance(ctx.get("dialogue_state"), dict) else {}
    intent = str(ctx.get("intent") or ds.get("last_intent") or "").strip().lower()
    if intent == "reasoning" and len(raw) >= 16:
        return True
    if len(raw) < _min_user_chars():
        return False
    mode = _loop_mode()
    tt = (task_tier or "").strip().lower()
    if mode == "tier":
        return tier_prefers_thorough(tt) or tt == "reasoning"
    return True


def _is_correction_pass(user_text: str) -> bool:
    return user_correcting_bot(user_text)


def _critique_system_prompt(is_correction: bool) -> str:
    base = (
        "Ты внутренний ревьюер ответа ассистента. Пользователь этого текста не увидит.\n"
        "Инженерный стиль: без «может быть», «возможно». Только факты и выводы.\n"
        "Кратко по-русски (или на языке запроса пользователя): список из 3–7 пунктов — "
        "пробелы в логике, непроверённые утверждения, риски неверной интерпретации, что уточнить.\n"
        "Без приветствий, без финального ответа пользователю, без TOOL_CALL."
    )
    if is_correction:
        base += (
            "\n\nВАЖНО: пользователь ИСПРАВЛЯЕТ предыдущий ответ ассистента. "
            "Проверь прежде всего: учтена ли поправка, не повторяет ли ответ ту же ошибку."
        )
    return base


def _final_system_prompt(is_correction: bool, system_for_passes: str) -> str:
    parts = [
        "Ты ассистент в чате. Пользователь видит только твой следующий ответ — один связный текст.\n"
        "Инженерный стиль: никаких «может быть», «возможно», «кажется». Ответ — это решение или факт. "
        "Trade-off — осознанный выбор, а не ошибка. Не извиняйся за архитектуру.\n"
        "Улучши ответ: закрой пробелы из ревью, не пересказывай ревью списком, не упоминай «ревьюер».\n"
        "Без TOOL_CALL, без внутренних рассуждений вслух, без английских цепочек «we need…»."
    ]
    if is_correction:
        parts.insert(0,
            "⚠️ Пользователь ИСПРАВЛЯЕТ твой предыдущий ответ. "
            "Приоритет №1: признать ошибку, учесть поправку, не повторять ту же ошибку. "
            "Не спорь, не оправдывайся, просто исправь."
        )
    return merge_system(*parts, TELEGRAM_PLAIN_REPLY_RULE, system_for_passes)


async def run_reasoning_loop_text_only(
    *,
    user_text: str,
    draft_reply: str,
    task_tier: str,
    telemetry_extra: Dict[str, Any],
    llm_session_id: str,
    system_for_passes: str,
    prof_secondary: Any,
) -> str:
    """
    Внутренние проходы: (1) критика черновика, (2) синтез финала для пользователя.
    Пользователь видит только итог второго прохода.

    Если пользователь исправляет бота — проходы фокусируются на коррекции.
    """
    draft = safe_text(draft_reply).strip()
    if not draft:
        return draft_reply
    ut = safe_text(user_text).strip()
    is_correction = _is_correction_pass(ut)
    MONITOR.inc("brain_reasoning_loop_total")
    if is_correction:
        MONITOR.inc("brain_reasoning_loop_correction_total")

    _sys_crit = merge_system(
        _critique_system_prompt(is_correction),
        TELEGRAM_PLAIN_REPLY_RULE,
    )
    crit_prompt = f"""Запрос пользователя:
{ut}

Черновик ответа ассистента:
{draft}

Дай только ревью (пункты), как указано в системной инструкции."""

    critique = ""
    try:
        if env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
            cr = await llm_generate_tiered(
                _llm,
                tag="llm_reasoning_loop_critique",
                prompt=crit_prompt,
                system_prompt=_sys_crit,
                max_tokens=_critique_max_tokens(),
                temperature=0.25,
                base_timeout=None,
                task_tier=task_tier,
                telemetry_tag="brain_reasoning_loop_critique",
                telemetry_extra=telemetry_extra,
                session_id=llm_session_id,
                conversation_id=llm_session_id,
            )
        else:
            cr = await with_timeout(
                _llm.generate(
                    prompt=crit_prompt,
                    system_prompt=_sys_crit,
                    max_tokens=_critique_max_tokens(),
                    temperature=0.25,
                    telemetry_tag="brain_reasoning_loop_critique",
                    telemetry_extra=telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                ),
                timeout_sec=DEFAULT_TIMEOUT_SEC,
                tag="llm_reasoning_loop_critique",
            )
        if not cr.get("error"):
            critique = strip_leaked_cot(
                safe_text(cr.get("content", "")),
                extra_markers_en=getattr(prof_secondary, "cot_extra_markers_en", ()) or (),
                extra_markers_ru=getattr(prof_secondary, "cot_extra_markers_ru", ()) or (),
            ).strip()
    except Exception as e:
        logger.debug("reasoning_loop critique: %s", e)

    if not critique:
        MONITOR.inc("brain_reasoning_loop_degraded_total")
        return draft_reply

    _sys_final = merge_system(
        _final_system_prompt(is_correction, system_for_passes),
    )
    final_prompt = f"""Запрос пользователя:
{ut}

Первый черновик ответа:
{draft}

Внутреннее ревью (не показывать пользователю дословно):
{critique}

Сформулируй один итоговый ответ пользователю."""

    try:
        if env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
            fn = await llm_generate_tiered(
                _llm,
                tag="llm_reasoning_loop_final",
                prompt=final_prompt,
                system_prompt=_sys_final,
                max_tokens=_final_max_tokens(),
                temperature=clamp_temperature(0.45, getattr(prof_secondary, "temperature_second_delta", 0.0)),
                base_timeout=None,
                task_tier=task_tier,
                telemetry_tag="brain_reasoning_loop_final",
                telemetry_extra=telemetry_extra,
                session_id=llm_session_id,
                conversation_id=llm_session_id,
            )
        else:
            fn = await with_timeout(
                _llm.generate(
                    prompt=final_prompt,
                    system_prompt=_sys_final,
                    max_tokens=_final_max_tokens(),
                    temperature=clamp_temperature(0.45, getattr(prof_secondary, "temperature_second_delta", 0.0)),
                    telemetry_tag="brain_reasoning_loop_final",
                    telemetry_extra=telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                ),
                timeout_sec=DEFAULT_TIMEOUT_SEC,
                tag="llm_reasoning_loop_final",
            )
        if fn.get("error"):
            return draft_reply
        out = strip_leaked_cot(
            safe_text(fn.get("content", "")),
            extra_markers_en=getattr(prof_secondary, "cot_extra_markers_en", ()) or (),
            extra_markers_ru=getattr(prof_secondary, "cot_extra_markers_ru", ()) or (),
        ).strip()
        try:
            from core.brain.reasoning_meta_strip import strip_reasoning_meta_leak

            out = strip_reasoning_meta_leak(out) or out
        except Exception as e:
            logger.debug("reasoning_meta_strip: %s", e)
        if not out or looks_like_repetition_glitch(out):
            return draft_reply
        MONITOR.inc("brain_reasoning_loop_ok_total")
        return out
    except Exception as e:
        logger.debug("reasoning_loop final: %s", e)
        return draft_reply
