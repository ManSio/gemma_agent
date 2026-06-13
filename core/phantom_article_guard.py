"""Guard: ellipsis по «статье» без paste — не уходить в LLM с выдуманным контентом."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PHANTOM_FOLLOWUP_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:у\s+)?(?:них|неё|него|неё|them|their)\b"
    r"|\bкакие\s+(?:у\s+)?(?:них|неё|него)\b"
    r"|\b(?:их|её|его)\s+реальн"
    r"|\bпроблем\w*"
    r"|\bнедостат\w*"
    r"|\bминус\w*"
    r"|\bограничен\w*"
    r")"
)


def phantom_article_guard_enabled() -> bool:
    """Whether to block LLM when article was requested but never pasted."""
    raw = (os.getenv("PHANTOM_ARTICLE_GUARD_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def session_requested_article_without_paste(recent_dialogue: Any) -> Optional[str]:
    """Return topic snippet if user asked about an article but never pasted body."""
    try:
        from core.brain.text_helpers import recent_dialogue_has_pasted_article

        if recent_dialogue_has_pasted_article(recent_dialogue):
            return None
    except Exception as e:
        logger.debug("phantom_article paste check: %s", e)
    try:
        from core.brain.profile_route_guard import text_mentions_article_context
    except Exception as e:
        logger.debug("phantom_article import: %s", e)
        return None

    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    topic = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in ("user", "human", ""):
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if not text:
            continue
        low = text.lower()
        if text_mentions_article_context(text):
            topic = text[:220]
        elif "стать" in low and len(text) <= 220 and not text.startswith("http"):
            topic = text[:220]
    return topic or None


def looks_like_phantom_article_followup(user_text: str) -> bool:
    """Ellipsis / «у них» follow-up without new article body."""
    t = (user_text or "").strip()
    if not t or len(t) > 160:
        return False
    if _PHANTOM_FOLLOWUP_RE.search(t):
        return True
    try:
        from core.article_thread_followup import (
            looks_like_article_thread_clarification,
            looks_like_article_thread_opinion_followup,
        )

        if looks_like_article_thread_opinion_followup(t):
            return True
        if looks_like_article_thread_clarification(t):
            return True
    except Exception as e:
        logger.debug("phantom_article followup cues: %s", e)
    return False


def phantom_article_guard_reply(*, topic: str, user_text: str = "") -> str:
    """Honest reply when there is no article text to discuss."""
    subj = (topic or "статья").strip()[:180]
    tail = (user_text or "").strip()[:80]
    if tail:
        return (
            f"Вы просили разобрать материал («{subj}»), но текста или ссылки в чате нет. "
            f"На «{tail}» без статьи ответить по существу не могу — пришлите текст или ссылку."
        )
    return (
        f"Вы просили разобрать материал («{subj}»), но текста или ссылки в чате нет. "
        "Пришлите статью — тогда смогу ответить по вашему уточнению."
    )


def should_phantom_article_guard(
    user_text: str,
    recent_dialogue: Any = None,
) -> bool:
    """True when follow-up assumes article content that was never provided."""
    if not phantom_article_guard_enabled():
        return False
    topic = session_requested_article_without_paste(recent_dialogue)
    if not topic:
        return False
    return looks_like_phantom_article_followup(user_text)


def try_phantom_article_guard_reply(
    user_text: str,
    *,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """Direct reply or None — caller continues to brain."""
    if not should_phantom_article_guard(user_text, recent_dialogue):
        return None
    topic = session_requested_article_without_paste(recent_dialogue) or "статья"
    try:
        from core.monitoring import MONITOR

        MONITOR.inc("phantom_article_guard_total")
    except Exception:
        pass
    return phantom_article_guard_reply(topic=topic, user_text=user_text)
