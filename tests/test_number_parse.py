from __future__ import annotations

from core.number_parse import parse_env_float, parse_loose_float


def test_parse_loose_float_narrow_nbsp_thousands() -> None:
    assert parse_loose_float("30\u202f000", 1.0) == 30000.0


def test_parse_env_float_monkeypatch(monkeypatch) -> None:
    monkeypatch.setenv("TEST_NUM_PARSE_X", "12\u202f500")
    assert parse_env_float("TEST_NUM_PARSE_X", 0.0) == 12500.0
