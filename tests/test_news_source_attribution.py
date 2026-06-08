"""Tests for core/news_article_model.py — source attribution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.news_article_model import (
    NewsArticle,
    NewsSource,
    build_news_article,
    build_news_source,
    legacy_article_to_news_article,
    make_news_context_for_llm,
    source_for_prompt,
)


class TestBuildNewsSource:
    """Verify NewsSource construction."""

    def test_build_news_source_creates_correct_structure(self) -> None:
        src = build_news_source(
            "https://example.com/news/123",
            fetch_method="urlfetch",
            title_used="Test Article",
            fetch_success=True,
            text_length=500,
            parsing_confidence=0.85,
        )
        assert src["url"] == "https://example.com/news/123"
        assert src["domain"] == "example.com"
        assert src["fetch_method"] == "urlfetch"
        assert src["title_used"] == "Test Article"
        assert src["fetch_success"] is True
        assert src["text_length"] == 500
        assert 0.0 <= src["parsing_confidence"] <= 1.0
        assert src["parsing_confidence"] == 0.85
        assert src["fetch_timestamp"]  # non-empty ISO string


class TestBuildNewsArticle:
    """Verify NewsArticle construction."""

    def test_build_news_article_creates_correct_structure(self) -> None:
        article = build_news_article(
            "Test Title",
            "Some article text content here",
            "https://example.com/article",
            fetch_method="urlfetch",
            images=["https://example.com/img.jpg"],
            confidence=0.75,
        )
        assert article["title"] == "Test Title"
        assert article["text"] == "Some article text content here"
        assert article["url"] == "https://example.com/article"
        assert article["source_domain"] == "example.com"
        assert article["fetch_method"] == "urlfetch"
        assert article["confidence"] == 0.75
        assert len(article["images"]) == 1
        assert article["images"][0] == "https://example.com/img.jpg"

    def test_build_news_article_domain_extraction(self) -> None:
        """Verify domain extracted correctly from various URL formats."""
        article = build_news_article(
            "Test", "text", "https://www.reuters.com/article/123",
        )
        assert article["source_domain"] == "reuters.com"

        article = build_news_article(
            "Test", "text", "https://bbc.co.uk/news/article",
        )
        assert article["source_domain"] == "bbc.co.uk"

        article = build_news_article("Test", "text", "")
        assert article["source_domain"] == ""

    def test_title_truncated_at_500(self) -> None:
        long_title = "A" * 1000
        article = build_news_article(long_title, "text", "https://example.com")
        assert len(article["title"]) <= 500

    def test_confidence_clamped(self) -> None:
        article = build_news_article(
            "Test", "text", "https://example.com", confidence=1.5,
        )
        assert article["confidence"] == 1.0

        article = build_news_article(
            "Test", "text", "https://example.com", confidence=-0.5,
        )
        assert article["confidence"] == 0.0


class TestSourceForPrompt:
    """Verify prompt formatting."""

    def test_source_for_prompt_includes_url(self) -> None:
        article = build_news_article(
            "Title", "text", "https://example.com/news",
            fetch_method="web_search", confidence=0.9,
        )
        prompt = source_for_prompt(article)
        assert "https://example.com/news" in prompt
        assert "Источник информации" in prompt
        assert "Надёжность парсинга: 90%" in prompt
        assert article["source_domain"] in prompt

    def test_make_news_context_for_llm(self) -> None:
        article = build_news_article(
            "Title", "This is the article body text",
            "https://example.com/article",
            confidence=0.8,
        )
        ctx = make_news_context_for_llm(article)
        assert "Текст:" in ctx
        assert "This is the article body text" in ctx
        assert "Источник информации:" in ctx
        assert "ПРАВИЛА:" in ctx
        assert "Опирайся ТОЛЬКО на текст выше" in ctx


class TestLegacyConversion:
    """Verify legacy dict to NewsArticle conversion."""

    def test_legacy_article_to_news_article_conversion(self) -> None:
        legacy = {
            "text": "Full article text with enough length for validation",
            "images": ["https://example.com/img.jpg"],
            "url": "https://example.com/article",
        }
        article = legacy_article_to_news_article(legacy)
        assert article is not None
        assert article["url"] == "https://example.com/article"
        assert article["source_domain"] == "example.com"
        assert article["fetch_method"] == "urlfetch"
        assert len(article["images"]) == 1
        assert article["confidence"] >= 0.3  # text length > 200

    def test_legacy_article_to_news_article_empty(self) -> None:
        legacy: dict = {}
        article = legacy_article_to_news_article(legacy)
        assert article is None

    def test_legacy_article_short_text_low_confidence(self) -> None:
        legacy = {"text": "short", "url": "https://example.com"}
        article = legacy_article_to_news_article(legacy)
        assert article is not None
        assert article["confidence"] == 0.3

    def test_legacy_article_no_url(self) -> None:
        legacy = {"text": "Some text but no url"}
        article = legacy_article_to_news_article(legacy)
        assert article is None


class TestNewsSourceEdgeCases:
    """Edge cases for build_news_source."""

    def test_source_with_empty_url(self) -> None:
        src = build_news_source(
            "", fetch_method="rss",
        )
        assert src["url"] == ""
        assert src["domain"] == ""

    def test_source_confidence_clamped(self) -> None:
        src = build_news_source(
            "https://example.com", fetch_method="rss",
            parsing_confidence=2.0,
        )
        assert src["parsing_confidence"] == 1.0

        src = build_news_source(
            "https://example.com", fetch_method="rss",
            parsing_confidence=-1.0,
        )
        assert src["parsing_confidence"] == 0.0

    def test_source_with_www_domain(self) -> None:
        src = build_news_source(
            "https://www.bbc.com/news", fetch_method="urlfetch",
        )
        assert src["domain"] == "bbc.com"