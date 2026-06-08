"""NewsArticle typed model with source attribution metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from typing_extensions import Literal, TypedDict

FetchMethod = Literal["rss", "web_search", "urlfetch"]


class NewsSource(TypedDict):
    """Один источник новости — URL, метаданные, качество парсинга."""

    url: str
    domain: str
    fetch_method: FetchMethod
    fetch_timestamp: str  # ISO 8601 UTC
    title_used: str
    fetch_success: bool
    text_length: int
    parsing_confidence: float  # 0.0–1.0


class NewsArticle(TypedDict):
    """Полная статья с верифицированными метаданными для передачи в LLM-контекст."""

    title: str
    text: str
    url: str
    fetch_timestamp: str  # ISO 8601 UTC
    source_domain: str
    fetch_method: FetchMethod
    confidence: float  # 0.0–1.0
    images: List[str]


def build_news_source(
    url: str,
    *,
    fetch_method: FetchMethod,
    title_used: str = "",
    fetch_success: bool = True,
    text_length: int = 0,
    parsing_confidence: float = 0.0,
) -> NewsSource:
    """Создать NewsSource с автозаполнением timestamp и domain."""
    return NewsSource(
        url=url,
        domain=_extract_domain(url),
        fetch_method=fetch_method,
        fetch_timestamp=datetime.now(timezone.utc).isoformat(),
        title_used=title_used[:300],
        fetch_success=fetch_success,
        text_length=text_length,
        parsing_confidence=max(0.0, min(1.0, parsing_confidence)),
    )


def build_news_article(
    title: str,
    text: str,
    url: str,
    *,
    fetch_method: FetchMethod = "urlfetch",
    images: Optional[List[str]] = None,
    confidence: float = 0.0,
) -> NewsArticle:
    """Создать NewsArticle с автозаполнением timestamp и domain."""
    return NewsArticle(
        title=title[:500],
        text=text,
        url=url,
        fetch_timestamp=datetime.now(timezone.utc).isoformat(),
        source_domain=_extract_domain(url),
        fetch_method=fetch_method,
        confidence=max(0.0, min(1.0, confidence)),
        images=images if images else [],
    )


def source_for_prompt(article: NewsArticle) -> str:
    """Форматировать метаданные для вставки в LLM-промпт."""
    return (
        f"Источник информации: {article['url']}\n"
        f"Дата парсинга: {article['fetch_timestamp']}\n"
        f"Домен: {article['source_domain']}\n"
        f"Надёжность парсинга: {article['confidence']:.0%}\n"
    )


NEWS_CONTEXT_TEMPLATE = (
    "Источник информации: {url}\n"
    "Дата парсинга: {fetch_timestamp}\n"
    "Надёжность парсинга: {confidence:.0%}\n"
    "\n"
    "Текст:\n"
    "{text}\n"
    "\n"
    "ПРАВИЛА:\n"
    "1. Опирайся ТОЛЬКО на текст выше\n"
    "2. Если неясно — скажи 'Источник не содержит этой информации'\n"
    "3. В ответе пользователю укажи источник одной строкой\n"
)


def make_news_context_for_llm(article: NewsArticle) -> str:
    """Собрать context block для вставки в LLM-промпт."""
    return NEWS_CONTEXT_TEMPLATE.format(
        url=article["url"],
        fetch_timestamp=article["fetch_timestamp"],
        confidence=article["confidence"],
        text=article["text"],
    )


def _extract_domain(url: str) -> str:
    """Извлечь домен из URL (example.com из https://www.example.com/path)."""
    if not url or not isinstance(url, str):
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host.startswith("www."):
            host = host[4:]
        return host.lower()
    except Exception:
        return ""


def legacy_article_to_news_article(
    legacy: Dict[str, Any],
    *,
    fetch_method: FetchMethod = "urlfetch",
) -> Optional[NewsArticle]:
    """Преобразовать legacy dict из _fetch_page_article в NewsArticle."""
    if not isinstance(legacy, dict):
        return None
    url = str(legacy.get("url") or "")
    text = str(legacy.get("text") or "")
    if not url or not text:
        return None
    confidence = 0.7 if len(text) > 200 else 0.3
    return build_news_article(
        title=legacy.get("title", ""),
        text=text,
        url=url,
        fetch_method=fetch_method,
        images=list(legacy.get("images") or []),
        confidence=confidence,
    )