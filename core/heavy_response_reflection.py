"""
Рефлексия ответа только на «тяжёлых» ходах (Recursive Companion — узко).

Не второй LLM на каждый чат: summarization, длинный вопрос, сбой quality gate, deep tier.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_EMPTY_FALLBACK = "Не удалось сформировать нормальный ответ. Повторите запрос короче или уточните задачу."

_REFLECTION_META_RE = re.compile(
    r"(?i)(черновик\s+ответа|улучши\s+черновик|профиль:\s*standard|"
    r"произошла\s+ошибка|явно\s+не\s+связан|проигнорир\w+\s+черновик|"
    r"инструкция\s+говорит|верни\s+только\s+улучшенный)"
)


def reflection_heavy_enabled() -> bool:
    return effective_bool("REFLECTION_HEAVY_ENABLED", default=True)


def _min_user_chars() -> int:
    try:
        return max(200, int((os.getenv("REFLECTION_HEAVY_MIN_USER_CHARS") or "400").strip()))
    except ValueError:
        return 400


def _heavy_profiles() -> frozenset:
    raw = (os.getenv("REFLECTION_HEAVY_PROFILES") or "summarization,research,document_qa,math_solve").strip()
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def _critical_pre_send_actions() -> frozenset:
    return frozenset(
        {
            "replace_fallback",
            "pre_send_recover",
            "append_truncation_note",
        }
    )


def should_skip_heavy_reflection_for_meta(
    output_meta: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Модули с фиксированным UX-контрактом — не переписывать черновик LLM-редактором."""
    om = output_meta or {}
    mod = str(om.get("module") or "").strip().lower()
    if mod == "spatial_design":
        return True
    if om.get("confirmation") or om.get("correction_ack") or om.get("no_mode_footer"):
        return True
    return False


def should_reflect_heavy_turn(
    *,
    user_text: str,
    reply: str,
    profile: str = "",
    task_tier: str = "",
    scenario_pre_hits: Optional[Sequence[Dict[str, Any]]] = None,
    tool_steps: int = 0,
    output_meta: Optional[Mapping[str, Any]] = None,
) -> bool:
    if not reflection_heavy_enabled():
        return False
    if should_skip_heavy_reflection_for_meta(output_meta):
        return False
    ut = (user_text or "").strip()
    rep = (reply or "").strip()
    if not ut or not rep:
        return False
    if rep == _EMPTY_FALLBACK or len(rep) < 12:
        return True
    prof = (profile or "standard").strip().lower()
    if prof in _heavy_profiles():
        return True
    tier = (task_tier or "").strip().lower()
    if tier in ("deep", "nested"):
        return True
    if len(ut) >= _min_user_chars():
        return True
    try:
        min_tools = max(1, int((os.getenv("REFLECTION_HEAVY_MIN_TOOL_STEPS") or "2").strip()))
    except ValueError:
        min_tools = 2
    if tool_steps >= min_tools:
        return True
    for h in scenario_pre_hits or ():
        if not isinstance(h, dict):
            continue
        if str(h.get("action") or "") == "code_fallback":
            return False
        if str(h.get("action") or "") in _critical_pre_send_actions():
            return True
        if str(h.get("severity") or "") == "critical":
            return True
    try:
        from core.brain.code_empty_recovery import code_reply_incomplete, user_requests_code

        if user_requests_code(ut) and code_reply_incomplete(ut, rep):
            return False
    except Exception as e:
        logger.debug("heavy_reflection code guard: %s", e)
    if effective_bool("REFLECTION_HEAVY_ON_LEAK", default=True):
        try:
            from core.brain.response_finalize import looks_like_prompt_instruction_leak

            if looks_like_prompt_instruction_leak(rep):
                return True
        except Exception as e:
            logger.debug('%s optional failed: %s', 'heavy_response_reflection', e, exc_info=True)
    try:
        from core.math_investment import text_looks_like_investment_annuity

        if text_looks_like_investment_annuity(ut):
            return False
    except Exception as e:
        logger.debug("heavy_reflection investment skip: %s", e)
    return False


def looks_like_reflection_meta_leak(text: str) -> bool:
    """Модель пересказала задание редактора вместо ответа пользователю."""
    s = (text or "").strip()
    if not s:
        return False
    if _REFLECTION_META_RE.search(s):
        return True
    try:
        from core.brain.response_finalize import looks_like_prompt_instruction_leak

        return looks_like_prompt_instruction_leak(s)
    except Exception:
        return False


async def refine_heavy_reply(
    *,
    user_text: str,
    reply: str,
    profile: str = "",
    user_id: str = "",
    session_id: str = "",
) -> str:
    """Один короткий проход fast-модели; при ошибке — исходный reply."""
    draft = (reply or "").strip()
    if not draft:
        return draft
    try:
        from core.llm_tiered import llm_generate_tiered
        from core.openrouter_provider import get_openrouter_provider

        llm = get_openrouter_provider()
        sys_p = (
            "Ты редактор ответа ассистента. Пользователь задал вопрос; черновик слабый или сбойный. "
            "Верни только улучшенный ответ пользователю на русском: по делу, без мета-комментариев, "
            "без XML, без «как ИИ». Не длиннее 1200 символов."
        )
        prompt = (
            f"Профиль: {profile or 'standard'}\n"
            f"Вопрос: {user_text[:2000]}\n\n"
            f"Черновик ответа:\n{draft[:3500]}\n\n"
            "Улучши черновик."
        )
        _sid = (session_id or "").strip()
        if not _sid and user_id:
            _sid = f"u-{user_id}.reflection_heavy"
        result = await llm_generate_tiered(
            llm,
            tag="reflection_heavy",
            prompt=prompt,
            system_prompt=sys_p,
            max_tokens=900,
            temperature=0.35,
            task_tier="shallow",
            session_id=_sid,
            conversation_id=_sid,
        )
        content = str(result.get("content") or result.get("text") or "").strip()
        if len(content) < 8:
            return draft
        if looks_like_reflection_meta_leak(content):
            return draft
        try:
            from core.brain.response_finalize import finalize_user_reply

            content = finalize_user_reply(content, user_text=user_text) or content
            if looks_like_reflection_meta_leak(content):
                return draft
        except Exception as e:
            logger.debug('%s optional failed: %s', 'heavy_response_reflection', e, exc_info=True)
        return content
    except Exception as e:
        logger.debug("refine_heavy_reply: %s", e)
        return draft


def maybe_apply_heavy_reflection_sync(
    reply: str,
    *,
    user_text: str,
    profile: str = "",
    task_tier: str = "",
    scenario_pre_hits: Optional[Sequence[Dict[str, Any]]] = None,
    tool_steps: int = 0,
    user_id: str = "",
) -> str:
    """Синхронная обёртка для тестов (без LLM)."""
    if not should_reflect_heavy_turn(
        user_text=user_text,
        reply=reply,
        profile=profile,
        task_tier=task_tier,
        scenario_pre_hits=scenario_pre_hits,
        tool_steps=tool_steps,
    ):
        return reply
    return reply
