"""
Опциональное LLM-сжатие выбывших из recent_messages реплик в связный абзац dialogue_summary.

По умолчанию выключено (DIALOGUE_COMPACT_LLM). Сначала behavior_store пишет мгновенный snippet;
оркестратор в фоне может заменить сводку на результат этого модуля, если файл не успел измениться.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from core.openrouter_provider import get_openrouter_provider
from core.resilience import with_timeout

logger = logging.getLogger(__name__)


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def format_overflow_for_prompt(messages: List[Dict[str, Any]], *, max_role_chars: int = 600) -> str:
    lines: List[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "?")
        text = str(m.get("text") or "").replace("\n", " ").strip()
        if len(text) > max_role_chars:
            text = text[: max_role_chars - 1] + "…"
        ts = m.get("telegram_ts")
        tsbit = f" ts={ts}" if ts is not None else ""
        lines.append(f"{role}{tsbit}: {text}")
    return "\n".join(lines)


async def compact_overflow_with_llm(
    *,
    prev_summary: str,
    overflow_messages: List[Dict[str, Any]],
) -> str:
    if not _env_truthy("DIALOGUE_COMPACT_LLM"):
        return ""
    if not overflow_messages:
        return ""
    if not (os.getenv("OPENROUTER_API_KEY") or "").strip():
        return ""

    body = format_overflow_for_prompt(overflow_messages)
    prev = (prev_summary or "").strip()
    prev_clip = prev[-2400:] if len(prev) > 2400 else prev
    prompt = (
        f"Предыдущая сводка (может быть пустой):\n{prev_clip}\n\n"
        f"Новые реплики:\n{body}\n\n"
        "Сожми смысл новых реплик в связку с предыдущей сводкой. "
        "Один короткий абзац на русском (2–5 предложений): темы, факты, договорённости, открытые вопросы. "
        "Не перечисляй роли «пользователь/бот», не выдумывай. Только текст абзаца."
    )
    model = (os.getenv("DIALOGUE_COMPACT_LLM_MODEL") or "").strip() or None
    try:
        max_tokens = max(80, min(400, int(os.getenv("DIALOGUE_COMPACT_MAX_TOKENS", "220"))))
    except ValueError:
        max_tokens = 220
    try:
        timeout_sec = max(8.0, min(60.0, float(os.getenv("DIALOGUE_COMPACT_TIMEOUT_SEC", "25"))))
    except ValueError:
        timeout_sec = 25.0

    llm = get_openrouter_provider()
    try:
        out = await with_timeout(
            llm.generate(
                prompt=prompt,
                system_prompt="Ты сжимаешь диалог. Ответь одним абзацем, без заголовков и списков.",
                model=model,
                max_tokens=max_tokens,
                temperature=0.25,
            ),
            timeout_sec=timeout_sec,
            tag="dialogue_compact_llm",
        )
    except Exception as e:
        logger.debug("dialogue compact LLM: %s", e)
        return ""

    if not isinstance(out, dict) or out.get("error"):
        return ""
    text = str(out.get("content") or "").strip()
    for prefix in ("Абзац:", "Сводка:", "Ответ:", "Итог:"):
        low = text.lower()
        pl = prefix.lower()
        if low.startswith(pl):
            text = text[len(prefix) :].strip()
            break
    return text[:2000]
