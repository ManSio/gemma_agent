"""Лексический recall по архиву."""

from core.lexical_dialog_recall import build_lexical_recall_hint, _tokens


def test_tokens():
    assert "небо" in _tokens("почему небо голубое")


def test_empty_on_short_query():
    assert build_lexical_recall_hint("u", None, "да") == ""
