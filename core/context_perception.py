"""
Снимок «как система видит ход» — для turns.jsonl и отладки, не для пользователя.

Не gate и не владелец хода (2026-05-25): не вешать на plan/orchestrator.
Реформа: `docs/BRAIN_CENTRIC_REFORM_PLAN_RU.md`.

Не заменяет LLM; фиксирует политику контекста: slim, archive, correction.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.runtime_telegram_settings import effective_bool


def build_context_perception(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    recent_dialogue: Any = None,
    input_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Детерминированный отчёт до вызова brain / shortcut."""
    text = (user_text or "").strip()
    rec = persisted if isinstance(persisted, dict) else {}
    meta = input_meta if isinstance(input_meta, dict) else {}
    rd = recent_dialogue if isinstance(recent_dialogue, list) else rec.get("recent_messages")
    if not isinstance(rd, list):
        rd = []

    rp = rec.get("routing_prefs") if isinstance(rec.get("routing_prefs"), dict) else {}
    pending_corr = rp.get("pending_correction") if isinstance(rp.get("pending_correction"), dict) else None
    pending_facts = rec.get("pending_facts_confirmation") if isinstance(rec.get("pending_facts_confirmation"), dict) else None

    try:
        from core.conversation_epoch import get_epoch_id

        epoch_id = get_epoch_id(rec)
    except Exception:
        epoch_id = 0

    archive_wanted = False
    archive_reason = ""
    try:
        from core.message_archive import should_backfill_dialogue_from_archive

        if should_backfill_dialogue_from_archive(
            user_text=text,
            recent_dialogue=rd,
            input_meta=meta,
        ):
            archive_wanted = True
            archive_reason = "backfill_heuristic"
    except Exception:
        pass

    slim_default = effective_bool("BRAIN_CHAT_CONTEXT_SLIM", default=True)
    mode = "slim" if slim_default else "standard"
    if pending_corr:
        mode = "correction_active"
    elif archive_wanted:
        mode = "archive_backfill"

    try:
        from core.brain.text_helpers import looks_like_pasted_news_article

        pasted_article = looks_like_pasted_news_article(text)
    except Exception:
        pasted_article = False

    return {
        "context_mode": mode,
        "recent_turns": len(rd),
        "conversation_epoch": epoch_id,
        "correction_pending": bool(pending_corr),
        "facts_confirm_pending": bool(pending_facts),
        "archive_backfill": archive_wanted,
        "archive_backfill_reason": archive_reason or None,
        "pasted_article": pasted_article,
        "has_attachment": bool(meta.get("has_telegram_attachment")),
        "slim_prompt_default": slim_default,
    }
