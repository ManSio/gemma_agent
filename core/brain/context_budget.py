"""Предупреждение о размере контекста до LLM (без auto-truncate)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def context_budget_warn_enabled() -> bool:
    raw = (os.getenv("BRAIN_CONTEXT_BUDGET_WARN_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def context_budget_warn_chars() -> int:
    try:
        v = int((os.getenv("BRAIN_CONTEXT_BUDGET_WARN_CHARS") or "12000").strip())
    except ValueError:
        v = 12000
    return max(2000, min(v, 80000))


def context_budget_user_note_enabled() -> bool:
    raw = (os.getenv("BRAIN_CONTEXT_BUDGET_USER_NOTE_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def estimate_prompt_chars(
    *,
    prompt: str,
    system_prompt: str = "",
    external_hint: str = "",
) -> int:
    return len(prompt or "") + len(system_prompt or "") + len(external_hint or "")


_CONTEXT_BUDGET_USER_NOTE_RU = (
    "Контекст диалога большой — для точности лучше /new или сформулируйте вопрос короче."
)


def stash_context_budget_user_note(
    context: dict,
    *,
    prompt: str,
    system_prompt: str = "",
    external_hint: str = "",
) -> None:
    """UX-3: одна подсказка в ответе, без auto-truncate."""
    if not isinstance(context, dict):
        return
    if not context_budget_warn_enabled() or not context_budget_user_note_enabled():
        return
    total = estimate_prompt_chars(
        prompt=prompt,
        system_prompt=system_prompt,
        external_hint=external_hint,
    )
    if total < context_budget_warn_chars():
        return
    context["_context_budget_user_note"] = _CONTEXT_BUDGET_USER_NOTE_RU
    maybe_log_context_budget_warning(tag="brain_pipeline", prompt=prompt, system_prompt=system_prompt)


def prepend_context_budget_user_note(context: dict, reply: str) -> str:
    if not isinstance(context, dict):
        return reply
    note = str(context.pop("_context_budget_user_note", "") or "").strip()
    body = (reply or "").strip()
    if not note or not body:
        return reply
    if note in body:
        return reply
    return f"{note}\n\n{body}"


def maybe_log_context_budget_warning(*, tag: str, prompt: str, system_prompt: str = "") -> None:
    if not context_budget_warn_enabled():
        return
    total = estimate_prompt_chars(prompt=prompt, system_prompt=system_prompt)
    limit = context_budget_warn_chars()
    if total < limit:
        return
    logger.warning(
        "context_budget tag=%s chars=%s limit=%s (no auto-truncate)",
        tag,
        total,
        limit,
        extra={"gemma_event": "context_budget_warn", "tag": tag, "chars": total},
    )
