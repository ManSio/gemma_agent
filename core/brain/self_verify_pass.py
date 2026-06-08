"""Self-verify pass для brain: быстрая модель проверяет ответ перед отправкой."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from core.autotune import is_self_verify_suppressed

logger = logging.getLogger(__name__)


def should_self_verify(profile: str, *, need_memory: bool = False) -> bool:
    """True если самопроверка включена и профиль подходит."""
    if profile == "batch":
        return False
    if os.getenv("SELF_VERIFY_ACTIVE", "false").lower() != "true":
        return False
    if is_self_verify_suppressed(profile):
        logger.debug("[self_verify] suppressed by autotune (profile=%s)", profile)
        return False
    from core.brain.profile_registry import profile_allows_self_verify

    return profile_allows_self_verify(profile, need_memory=need_memory)


def looks_like_garbage_json(text: str) -> bool:
    """True если ответ — сырой JSON (не TOOL_CALL), ушедший пользователю."""
    s = (text or "").strip()
    if not s:
        return False
    if s.startswith("TOOL_CALL:"):
        return False
    if s.startswith("{") and '"' in s and ":" in s:
        return True
    if s.startswith("[") and ("{" in s or (len(s) > 40 and not re.search(r"[а-яё]", s))):
        return True
    return False


def self_verify_fix_quality(fix_text: str) -> bool:
    """Проверка, что исправление self-verify — осмысленное предложение."""
    txt = (fix_text or "").strip()
    if len(txt) < 50:
        return False
    has_upper = any(c.isupper() for c in txt)
    has_period = "." in txt
    if not has_upper and not has_period:
        return False
    return True


def self_verify_model_id() -> str:
    return (os.getenv("SELF_VERIFY_MODEL") or "google/gemma-3-12b-it").strip()


def self_verify_timeout() -> float:
    try:
        return max(5.0, float(os.getenv("SELF_VERIFY_TIMEOUT_SEC", "15.0").strip()))
    except (ValueError, TypeError):
        return 15.0


async def run_self_verify(
    reply: str,
    user_text: str,
    llm: Any,
    *,
    clock_info: str = "",
    user_name: str = "",
    source_context: str = "",
) -> str:
    """
    Возвращает:
      - "ok" — проблем не найдено
      - "fix: <текст>" — предложенное исправление

    source_context: контекст источников новостей (URL, timestamp, confidence).
    """
    grounding_lines = []
    if clock_info:
        grounding_lines.append(f"Реальное текущее время на сервере: {clock_info}")
    else:
        from datetime import datetime, timezone

        grounding_lines.append(
            f"Реальное текущее время на сервере: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
    if user_name:
        grounding_lines.append(f"Имя пользователя (из user_facts): {user_name}")
    if source_context:
        grounding_lines.append(f"\nИсточники:\n{source_context}")
    grounding = "\n".join(grounding_lines)
    if source_context:
        verify_prompt = (
            "Проверь ответ ассистента на фактические ошибки, галлюцинации и логические дыры.\n"
            "Используй факты ниже как истину — не гадай дату/время и не выдумывай имя.\n"
            "КРИТИЧЕСКИ ВАЖНО: Ответ ассистента ОБЯЗАН опираться ТОЛЬКО на источники выше.\n"
            "Если ассистент приводит факты, которых нет в источниках — это галлюцинация.\n"
            "Если всё корректно — ответь ровно одним словом: ok\n"
            "Если есть проблема — ровно одной строкой в формате:\n"
            "fix: <исправленный_текст>\n"
            "Без пояснений, без лишнего форматирования.\n"
            f"\nФакты:\n{grounding}\n"
            f"\nВопрос пользователя:\n{user_text}\n"
            f"\nОтвет ассистента:\n{reply}"
        )
    else:
        verify_prompt = (
            "Проверь ответ ассистента на ошибки, галлюцинации и логические дыры.\n"
            "Используй факты ниже как истину — не гадай дату/время и не выдумывай имя.\n"
            "Если всё корректно — ответь ровно одним словом: ok\n"
            "Если есть проблема — ровно одной строкой в формате:\n"
            "fix: <исправленный_текст>\n"
            "Без пояснений, без лишнего форматирования.\n"
            f"\nФакты:\n{grounding}\n"
            f"\nВопрос пользователя:\n{user_text}\n"
            f"\nОтвет ассистента:\n{reply}"
        )
    model = self_verify_model_id()
    timeout = self_verify_timeout()
    try:
        result = await asyncio.wait_for(
            llm.generate(
                prompt=verify_prompt,
                system_prompt="Ты — строгий верификатор. Исправляй фактические ошибки.",
                model=model,
                max_tokens=1500,
                temperature=0.1,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("[self_verify] timeout (model=%s)", model)
        return "ok"
    except Exception as e:
        logger.warning("[self_verify] error: %s", e)
        return "ok"

    content = str(result.get("content", "") or "").strip()
    if not content:
        return "ok"

    low = content.lower()
    if low.startswith("fix:"):
        fix_text = content[4:].strip()
        if fix_text:
            logger.info("[self_verify] fix candidate: %.100s", fix_text)
            return f"fix: {fix_text}"
        logger.warning("[self_verify] empty fix, ignoring")
        return "ok"

    return "ok"


async def retry_with_fix_hint(
    user_text: str,
    bad_reply: str,
    fix_hint: str,
    llm: Any,
    *,
    system_prompt_for_llm: str,
    kv_cache_tail: str,
) -> str:
    """Повторный запрос к основной модели с подсказкой о проблеме."""
    hint = (
        f"Пользователь (через верификатор) указал на проблему в твоём ответе:\n"
        f"{fix_hint}\n\n"
        f"Исправленный вариант должен устранить эту проблему. "
        f"Не повторяй старый ответ."
    )
    retry_prompt = (
        f"Вопрос пользователя:\n{user_text}\n\n"
        f"Твой предыдущий (проблемный) ответ:\n{bad_reply}\n\n"
        f"Замечание:\n{hint}\n\n"
        f"Дай исправленный ответ."
    )
    try:
        if os.getenv("BRAIN_LLM_TIERED_RETRY", "true").strip().lower() in {"1", "true", "yes", "on"}:
            from core.llm_tiered import llm_generate_tiered

            result = await llm_generate_tiered(
                llm,
                tag="llm_self_verify_retry",
                prompt=retry_prompt,
                system_prompt=system_prompt_for_llm,
                max_tokens=2000,
                temperature=0.3,
                base_timeout=30.0,
                kv_cache_tail=kv_cache_tail,
                telemetry_tag="brain_self_verify_retry",
            )
        else:
            result = await llm.generate(
                prompt=retry_prompt,
                system_prompt=system_prompt_for_llm,
                max_tokens=2000,
                temperature=0.3,
                kv_cache_tail=kv_cache_tail,
            )
    except Exception as e:
        logger.warning("[self_verify] retry error: %s", e)
        return bad_reply

    new_text = str(result.get("content", "") or "").strip()
    if not new_text or new_text == bad_reply:
        return bad_reply
    logger.info("[self_verify] retry produced new reply: %.100s", new_text)
    return new_text
