"""Tests for core/news_disclaimer.py."""

import pytest
from core.news_disclaimer import NewsDisclaimerGenerator, format_news_with_disclaimer


@pytest.fixture
def gen():
    return NewsDisclaimerGenerator()


def _s(domain, conf=0.8):
    return {"domain": domain, "parsing_confidence": conf}


def test_generate_high_confidence_all_trusted(gen):
    srcs = [_s("reuters.com", 0.95), _s("bbc.com", 0.90)]
    r = gen.generate(srcs)
    # Используется 📰 (U+1F4F0), а не 📘
    assert "\U0001f4f0" in r
    assert "\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a" in r
    assert "\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f" in r
    assert "\u26a0" not in r


def test_generate_medium_confidence(gen):
    srcs = [_s("reuters.com", 0.6), _s("unknown.example", 0.4)]
    r = gen.generate(srcs)
    assert "\u26a0" in r
    assert "1/2" in r


def test_generate_low_confidence(gen):
    srcs = [_s("unknown.example", 0.1), _s("bad.site", 0.2)]
    r = gen.generate(srcs)
    assert "\u26a0" in r
    assert "\u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u043c" in r


def test_generate_empty_sources(gen):
    r = gen.generate([])
    assert "\u26a0" in r
    assert "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435" in r or "\u043f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435" in r


def test_generate_for_single_source_trusted(gen):
    r = gen.generate_for_single_source("https://reuters.com/1", "reuters.com", confidence=0.8)
    assert "\U0001f4f0" in r
    assert NewsDisclaimerGenerator.is_trusted_domain("reuters.com")


def test_generate_for_single_source_untrusted(gen):
    r = gen.generate_for_single_source("https://unknown.x/p", "unknown.x", confidence=0.3)
    assert "\u26a0" in r
    assert "\u0420\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0443\u0435\u043c" in r


def test_format_news_with_disclaimer():
    srcs = [_s("reuters.com", 0.9)]
    r = format_news_with_disclaimer("Test body content.", srcs, user_query="q")
    assert "Test body content" in r


def test_format_news_with_disclaimer_empty_body():
    assert format_news_with_disclaimer("   ", [_s("r.com", 0.9)]) == ""
    assert format_news_with_disclaimer("", []) == ""


@pytest.mark.parametrize("domain,exp", [
    ("reuters.com", True), ("bbc.com", True), ("ap.org", True),
    ("www.reuters.com", True), ("lenta.ria.ru", True),
    ("unknown.example", False), ("", False), ("myblog.ru", False),
])
def test_is_trusted_domain(domain, exp):
    assert NewsDisclaimerGenerator.is_trusted_domain(domain) is exp
