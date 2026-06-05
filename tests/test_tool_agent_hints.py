"""Подсказки инструментов, валидация TOOL_CALL, дедуп кэша."""

from __future__ import annotations

from core.brain.text_helpers import (
    parse_tool_call,
    strip_chat_markdown_for_telegram,
    strip_leaked_tool_call_markup,
    tool_call_marker_body_incomplete,
)
from core.brain.tool_call_support import (
    describe_tool_call_retry_issue,
    prioritize_tools_by_hint,
    text_before_tool_call,
    tool_call_validation_error,
)
from core.brain.tool_dedup import cache_key, dedup_enabled, lookup, store
from core.brain.tool_routing_hint import build_tool_routing_hint


def test_prioritize_tools_by_hint_order():
    tools = {"A": "x", "B": "y", "UrlFetch.fetch_page": "z"}
    out = prioritize_tools_by_hint(tools, ["UrlFetch.fetch_page", "Missing"])
    assert list(out.keys())[0] == "UrlFetch.fetch_page"
    assert set(out.keys()) == set(tools.keys())


def test_tool_call_validation():
    allowed = {"UrlFetch.fetch_page", "UniversalSearch.search"}
    assert tool_call_validation_error({}, allowed) == ""
    assert "пустое" in tool_call_validation_error({"name": "", "args": {}}, allowed)
    assert "не из" in tool_call_validation_error(
        {"name": "Nope.tool", "args": {}}, allowed
    )
    assert tool_call_validation_error({"name": "UrlFetch.fetch_page", "args": {"url": "x"}}, allowed) == ""
    assert "args" in tool_call_validation_error(
        {"name": "UrlFetch.fetch_page", "args": []}, allowed
    )


def test_text_before_tool_call():
    t = 'Привет.\n\nTOOL_CALL:\n{"name": "X", "args": {}}'
    assert "Привет" in text_before_tool_call(t)
    assert "TOOL_CALL" not in text_before_tool_call(t)


def test_text_before_tool_call_xml():
    t = "Ищу.\n<tool_call>LawSearch.search\n<arg_key>q</arg_key><arg_value>x</arg_value>\n</tool_call>"
    out = text_before_tool_call(t)
    assert "Ищу" in out
    assert "tool_call" not in out.lower()


def test_strip_chat_markdown_removes_simple_html():
    t = strip_chat_markdown_for_telegram("Важно: <b>текст</b> и <code>x</code>")
    assert "<b>" not in t and "<code>" not in t
    assert "текст" in t


def test_tool_call_marker_body_incomplete_truncated():
    t = 'TOOL_CALL:\n{"name": "UrlFetch.fetch_page", "args": {"url": "https://example.com/x'
    assert tool_call_marker_body_incomplete(t)


def test_describe_tool_call_retry_issue_truncated():
    allowed = {"UrlFetch.fetch_page"}
    t = 'Проверяю.\nTOOL_CALL:\n{"name": "UrlFetch.fetch_page", "args": {"url": "https://a'
    msg = describe_tool_call_retry_issue(t, allowed)
    assert msg
    assert "обрезан" in msg.lower() or "json" in msg.lower()


def test_parse_tool_call_accepts_tool_field_as_name():
    t = 'TOOL_CALL:\n{"tool":"UserKnowledgeArchive.archive_search","args":{"query":"x"}}'
    tc = parse_tool_call(t)
    assert tc.get("name") == "UserKnowledgeArchive.archive_search"
    assert tc["args"]["query"] == "x"


def test_parse_tool_call_first_json_only_extra_lines():
    """Модель иногда добавляет текст после JSON — берём первый объект (raw_decode)."""
    t = 'TOOL_CALL:\n{"name": "X.y", "args": {}}\nещё текст'
    tc = parse_tool_call(t)
    assert tc.get("name") == "X.y"
    assert tc.get("args") == {}


def test_parse_tool_call_xmlish_lawsearch():
    t = """Ищу информацию.
<tool_call>LawSearch.search
<arg_key>query</arg_key>
<arg_value>декрет строительство многодетных Республика Беларусь</arg_value>
<arg_key>source</arg_key>
<arg_value>etal</arg_value>
</tool_call>"""
    tc = parse_tool_call(t)
    assert tc.get("name") == "LawSearch.search"
    assert tc["args"]["query"]
    assert tc["args"]["source"] == "etal"


def test_strip_leaked_tool_call_markup():
    t = "Вступление.\n<tool_call>X.y\n<arg_key>a</arg_key><arg_value>b</arg_value>\n</tool_call>"
    assert "tool_call" not in strip_leaked_tool_call_markup(t).lower()
    assert "Вступление" in strip_leaked_tool_call_markup(t)


def test_routing_hint_url():
    h = build_tool_routing_hint(
        "прочитай",
        ["https://example.com/doc"],
        {"UrlFetch.fetch_page", "UniversalSearch.search"},
    )
    assert "UrlFetch.fetch_page" in h.suggested
    assert h.prompt_note


def test_routing_hint_exploratory_law_puts_universal_search_first():
    allowed = {
        "UniversalSearch.search",
        "DocumentCorpus.unified_search",
        "UrlFetch.fetch_page",
    }
    h = build_tool_routing_hint(
        'найди всё про законах «ответственность за тишину»',
        [],
        allowed,
    )
    assert h.suggested[0] == "UniversalSearch.search"
    assert "LawSearch.search" not in h.suggested


def test_routing_hint_recipe_prefers_universal_search():
    allowed = {"UniversalSearch.search", "Wikipedia.scan", "UrlFetch.fetch_page"}
    h = build_tool_routing_hint(
        "Как приготовить демьянку из баклажанов?",
        [],
        allowed,
    )
    assert h.suggested[0] == "UniversalSearch.search"
    assert "кулинар" in h.prompt_note.lower() or "рецепт" in h.prompt_note.lower()


def test_routing_hint_textbook_uses_universal_search():
    allowed = {
        "BooksRAG.search_book",
        "UniversalSearch.search",
        "UrlFetch.fetch_page",
    }
    h = build_tool_routing_hint("найди учебник по биологии", [], allowed)
    assert "UniversalSearch.search" in h.suggested


def test_routing_hint_edu_url_uses_urlfetch():
    h = build_tool_routing_hint(
        "открой",
        ["https://edu.example.com/"],
        {
            "UrlFetch.fetch_page",
            "UniversalSearch.search",
        },
    )
    assert h.suggested[0] == "UrlFetch.fetch_page"


def test_dedup_roundtrip(monkeypatch):
    monkeypatch.setenv("BRAIN_TOOL_DEDUP_ENABLED", "true")
    monkeypatch.setenv("BRAIN_TOOL_DEDUP_TTL_SEC", "30")
    assert dedup_enabled() is True
    uid = "u_test_dedup"
    store(uid, "UniversalSearch.search", {"query": "foo"}, {"ok": True, "hits": 1})
    got = lookup(uid, "UniversalSearch.search", {"query": "foo"})
    assert isinstance(got, dict) and got.get("ok") is True
    assert lookup(uid, "UrlFetch.fetch_page", {"url": "https://x"}) is None


def test_wikipedia_dedup_key_splits_by_lang():
    a = cache_key("Wikipedia.scan", {"query": "Ждановічы", "lang": "be"})
    b = cache_key("Wikipedia.scan", {"query": "Ждановічы", "lang": "ru"})
    assert a and b and a != b


def test_dedup_skips_errors(monkeypatch):
    monkeypatch.setenv("BRAIN_TOOL_DEDUP_ENABLED", "true")
    uid = "u_test_dedup2"
    store(uid, "UniversalSearch.search", {"query": "bar"}, {"error": "fail"})
    assert lookup(uid, "UniversalSearch.search", {"query": "bar"}) is None
