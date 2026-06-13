"""Bounded regex helpers for user-controlled text (ReDoS guard)."""
from __future__ import annotations

import os
import re
from typing import Any, Match, Optional, Pattern, Union

_Pattern = Union[str, Pattern[str]]


def regex_input_max_len() -> int:
    """Max input length for regex on user-controlled strings."""
    raw = (os.getenv("REGEX_INPUT_MAX_LEN") or "4096").strip()
    try:
        return max(64, int(raw))
    except ValueError:
        return 4096


def cap_regex_input(text: Any, *, max_len: Optional[int] = None) -> str:
    """Truncate text before regex to bound worst-case match time."""
    s = str(text or "")
    cap = max_len if max_len is not None else regex_input_max_len()
    return s if len(s) <= cap else s[:cap]


def strip_trailing_sentence_punct(text: str) -> str:
    """Drop trailing sentence punctuation without regex backtracking."""
    return (text or "").strip().rstrip(".!?…").strip()


def collapse_whitespace(text: str) -> str:
    """Normalize runs of whitespace without ambiguous regex."""
    return " ".join((text or "").split())


def safe_re_search(
    pattern: _Pattern,
    text: Any,
    flags: int = 0,
    *,
    max_len: Optional[int] = None,
) -> Optional[Match[str]]:
    """re.search on capped user text."""
    t = cap_regex_input(text, max_len=max_len)
    cap = max_len if max_len is not None else regex_input_max_len()
    if len(t) > cap:
        t = t[:cap]
    if isinstance(pattern, re.Pattern):
        return pattern.search(t)
    return re.search(pattern, t, flags)


def safe_re_match(
    pattern: _Pattern,
    text: Any,
    flags: int = 0,
    *,
    max_len: Optional[int] = None,
) -> Optional[Match[str]]:
    """re.match on capped user text."""
    t = cap_regex_input(text, max_len=max_len)
    cap = max_len if max_len is not None else regex_input_max_len()
    if len(t) > cap:
        t = t[:cap]
    if isinstance(pattern, re.Pattern):
        return pattern.match(t)
    return re.match(pattern, t, flags)


def safe_re_sub(
    pattern: _Pattern,
    repl: Any,
    text: Any,
    count: int = 0,
    flags: int = 0,
    *,
    max_len: Optional[int] = None,
) -> str:
    """re.sub on capped user text."""
    t = cap_regex_input(text, max_len=max_len)
    cap = max_len if max_len is not None else regex_input_max_len()
    if len(t) > cap:
        t = t[:cap]
    if isinstance(pattern, re.Pattern):
        return pattern.sub(repl, t, count=count)
    return re.sub(pattern, repl, t, count=count, flags=flags)
