"""
Детерминированные правила профиля до/после LLM-роутера.

Цель: ссылки на статьи, длинные вставки и обсуждение архитектуры не уходят в math_solve /
translation / operational_diag.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from core.brain.profile_registry import is_valid_profile, normalize_profile

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)

_ARTICLE_URL_MARKERS = (
    "habr.com",
    "habr.ru",
    "medium.com",
    "vc.ru",
    "dev.to",
    "arxiv.org",
    "wikipedia.org",
    "github.io",
    "tproger.ru",
    "dou.ua",
)

_ARTICLE_TEXT_MARKERS = (
    "из статьи",
    "в статье",
    "по статье",
    "статья про",
    "статью про",
    "сравнительная таблица",
    "перескаж",
    "суммариз",
    "кратко перескаж",
    "что в тексте",
    "что в статье",
    "прочитай стать",
    "разбор стать",
)

_REWRITE_SUMMARY_MARKERS = (
    "перепиши",
    "кратко",
    "сократи",
    "резюме",
    "краткая версия",
    "сохранив смысл",
)

_CHITCHAT_SHORT_RE = re.compile(
    r"(?i)^\s*(привет|здравств|добрый|как\s+дела|hi|hello|hey|спасибо|пока)\b"
)

_ARCHITECTURE_DISCUSSION_MARKERS = (
    "experience_digest",
    "strategy_paths",
    "gemma_bot",
    "qdrant",
    "ragas",
    "rag-систем",
    "rag систем",
    "микро-rag",
    "микро‑rag",
    "route_risk_cluster",
    "reputation/",
    "math_reasoning",
    "urlfetch",
    "openrouter_provider",
)


def extract_urls(text: str) -> List[str]:
    return [_normalize_url(u) for u in _URL_RE.findall(text or "")]


def _normalize_url(u: str) -> str:
    return u.rstrip(").,;]\"'")


def is_url_only_message(text: str) -> bool:
    """Сообщение — по сути одна или несколько ссылок (без смысловой реплики)."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    urls = extract_urls(stripped)
    if not urls:
        return False
    remainder = stripped
    for u in urls:
        remainder = remainder.replace(u, "")
    remainder = re.sub(r"[\s\W_]+", "", remainder, flags=re.UNICODE)
    return len(remainder) < 8


def url_looks_like_article(url: str) -> bool:
    low = (url or "").lower()
    if any(h in low for h in _ARTICLE_URL_MARKERS):
        return True
    return bool(re.search(r"/(?:articles?|post|blog|news|companies/[^/]+/articles?)/", low))


def _prose_without_urls(text: str) -> str:
    out = text or ""
    for u in extract_urls(out):
        out = out.replace(u, " ")
    return out


def text_mentions_article_context(text: str) -> bool:
    """Явный запрос про статью в реплике, не факт встроенной ссылки."""
    low = (text or "").lower()
    if any(m in low for m in _ARTICLE_TEXT_MARKERS):
        return True
    prose = _prose_without_urls(text).lower()
    if "habr" in prose and ("стать" in prose or "статья" in prose):
        return True
    return False


def looks_like_architecture_or_long_form_discussion(text: str) -> bool:
    """Длинная вставка про устройство бота / RAG — не operational_diag и не math."""
    raw = (text or "").strip()
    if len(raw) < 350:
        return False
    low = raw.lower()
    hits = sum(1 for m in _ARCHITECTURE_DISCUSSION_MARKERS if m in low)
    if hits >= 2:
        return True
    if hits >= 1 and ("в статье" in low or "из статьи" in low or "rag" in low):
        return True
    return False


def explicit_math_profile_allowed(text: str) -> bool:
    try:
        from core.intent_heuristics import (
            explicit_math_request,
            strip_urls_and_mentions_for_math_probe,
        )

        raw = (text or "").strip()
        if not raw:
            return False
        scrubbed = strip_urls_and_mentions_for_math_probe(raw)
        return explicit_math_request(raw, scrubbed)
    except Exception:
        return False


def preflight_profile(user_text: str) -> Optional[str]:
    """
    Жёсткий профиль до LLM-роутера (bypass / LRU / LLM).
    None — отдать классификатору.
    """
    txt = (user_text or "").strip()
    if not txt:
        return None

    urls = extract_urls(txt)
    if urls:
        if is_url_only_message(txt):
            return "summarization"
        if all(url_looks_like_article(u) for u in urls):
            if len(txt) < 320 or text_mentions_article_context(txt) or is_url_only_message(txt):
                return "summarization"
        if len(txt) < 120 and any(url_looks_like_article(u) for u in urls):
            return "summarization"

    if looks_like_architecture_or_long_form_discussion(txt):
        return "quick_explain"

    low = txt.lower()
    if any(m in low for m in _REWRITE_SUMMARY_MARKERS):
        if len(txt) > 80 or "заметк" in low or "текст" in low or "стать" in low:
            return "summarization"

    if _CHITCHAT_SHORT_RE.match(txt) and len(txt) < 48:
        return "short"

    if len(txt) > 400 and text_mentions_article_context(txt):
        if not explicit_math_profile_allowed(txt):
            return "quick_explain"

    if len(txt) > 900 and not explicit_math_profile_allowed(txt):
        return "quick_explain"

    return None


_PROFILES_NEVER_ON_ARTICLE = frozenset(
    {"math_solve", "translation", "legal", "data_analysis"}
)


def clamp_profile(
    profile: str,
    user_text: str,
    *,
    router_confidence: float = 0.5,
) -> str:
    """После роутера / refine: не оставлять опасные профили на статьях и простынях."""
    p = normalize_profile(profile or "standard")
    if not is_valid_profile(p):
        p = "standard"

    pre = preflight_profile(user_text)
    if pre:
        return pre

    txt = (user_text or "").strip()
    urls = extract_urls(txt)

    if p in _PROFILES_NEVER_ON_ARTICLE:
        if urls and any(url_looks_like_article(u) for u in urls):
            return "summarization"
        if looks_like_architecture_or_long_form_discussion(txt):
            return "quick_explain"
        if len(txt) > 200 and text_mentions_article_context(txt):
            return "quick_explain"
        if p == "math_solve" and not explicit_math_profile_allowed(txt):
            if len(txt) > 80:
                return "quick_explain"

    if p == "translation" and len(txt) > 180:
        low = txt.lower()
        if not re.search(r"(?i)(?:^|\n)\s*(?:переведи|translate)\b", txt):
            if not re.search(r"(?i)^перевод\s", low):
                return "quick_explain" if len(txt) > 400 else "standard"

    if p == "legal" and len(txt) > 200:
        low = txt.lower()
        if "статья " in low and not re.search(
            r"(?i)(?:^|\n|\.)\s*(?:закон|нпа|кодекс|pravo\.by)",
            txt,
        ):
            if text_mentions_article_context(txt) or looks_like_architecture_or_long_form_discussion(txt):
                return "quick_explain"

    if p == "math_solve" and router_confidence >= 0.9 and not explicit_math_profile_allowed(txt):
        if len(txt) > 60 or urls:
            return "summarization" if urls else "quick_explain"

    if p in ("code_debug", "legal"):
        try:
            from core.heuristic_context_gate import should_run_shortcut

            rule_id = "profile_code_debug_word" if p == "code_debug" else "profile_legal_substring"
            if not should_run_shortcut(rule_id, txt).allowed:
                return "quick_explain"
        except Exception as e:
            logger.debug("clamp_profile gate %s: %s", p, e)

    if p in ("code_generation", "code_debug"):
        low = txt.lower()
        if _CHITCHAT_SHORT_RE.match(txt) and len(txt) < 48:
            return "short"
        if any(m in low for m in _REWRITE_SUMMARY_MARKERS) and "код" not in low and "python" not in low:
            return "summarization"
        try:
            from core.brain.code_empty_recovery import user_requests_code

            if p == "code_generation" and not user_requests_code(txt):
                return "quick_explain"
        except Exception as e:
            logger.debug("clamp_profile code_generation: %s", e)

    return p
