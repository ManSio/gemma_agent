"""Маршрутизация skill intent: эвристики + опциональный короткий LLM."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SKILL_LIKE_RE = re.compile(
    r"(?i)(перевед|translate|объясни|explain|расписан|schedule|напомни|remind|"
    r"код|code|debug|бюджет|финанс|рецепт|recipe|погод|weather|"
    r"по[-\s]?(?:английск|русск|немецк|французск)|"
    r"\b(?:english|german|french)\s*:)"
)


def skill_router_llm_enabled() -> bool:
    raw = (os.getenv("SKILL_ROUTER_LLM_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _known_skill_names() -> List[str]:
    try:
        from core.brain.runtime import _skills

        return sorted(_skills.status().keys())
    except Exception:
        return []


async def resolve_skill_intent(user_text: str) -> Optional[str]:
    """Эвристика; при неоднозначности — короткий LLM-выбор из зарегистрированных скиллов."""
    from modules.skills.router import detect_skill_intent

    text = (user_text or "").strip()
    if not text:
        return None
    heuristic = detect_skill_intent(text)
    if heuristic:
        return heuristic
    try:
        from core.brain.text_helpers import task_fact_profile

        prof = task_fact_profile(text, {}, None)
        if prof.get("is_weather") or prof.get("is_news"):
            return None
    except Exception as e:
        logger.debug("skill_router weather/news skip: %s", e)
    if not skill_router_llm_enabled() or not _SKILL_LIKE_RE.search(text):
        return None
    names = _known_skill_names()
    if not names:
        return None
    try:
        from core.brain.runtime import _llm
        from core.llm_tiered import llm_generate_tiered

        catalog = ", ".join(names[:40])
        sys_prompt = (
            "Выбери один skill id из списка или ответь NONE. "
            "Ответь только одним токеном: имя skill или NONE."
        )
        user_prompt = f"Список skills: {catalog}\n\nЗапрос пользователя:\n{text[:800]}"
        out = await llm_generate_tiered(
            _llm,
            tag="skill_router",
            prompt=user_prompt,
            system_prompt=sys_prompt,
            max_tokens=24,
            temperature=0.0,
        )
        raw = str((out or {}).get("content") or "").strip().lower()
        raw = raw.split()[0].strip(".,;:\"'") if raw else ""
        if raw in {"none", "-", "нет"}:
            return None
        if raw in names:
            return raw
        for n in names:
            if n in raw or raw in n:
                return n
    except Exception as e:
        logger.debug("skill_router llm: %s", e)
    return None


def resolve_skill_intent_sync(user_text: str) -> Optional[str]:
    """Синхронный fallback (только эвристики) — для sync-контекстов."""
    from modules.skills.router import detect_skill_intent

    return detect_skill_intent(user_text or "")
