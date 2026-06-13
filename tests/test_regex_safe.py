"""regex_safe helpers."""
from __future__ import annotations

from core.regex_safe import (
    cap_regex_input,
    collapse_whitespace,
    strip_trailing_sentence_punct,
)


def test_cap_regex_input_truncates() -> None:
    assert len(cap_regex_input("x" * 100, max_len=20)) == 20


def test_strip_trailing_sentence_punct() -> None:
    assert strip_trailing_sentence_punct("почему?...") == "почему"


def test_collapse_whitespace() -> None:
    assert collapse_whitespace("a   b\n c") == "a b c"
