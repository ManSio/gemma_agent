"""Юнит-тесты сессии загрузки site recipe (без Telegram)."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.site_recipe_upload_session import (
    append_item,
    cancel_session,
    extract_host,
    get_session,
    max_batch,
    start_session,
    try_parse_recipe_file,
)


@pytest.fixture(autouse=True)
def _clear_sessions():
    from core import site_recipe_upload_session as m

    m._SESSIONS.clear()
    m._DEFER_NORMAL.clear()
    yield
    m._SESSIONS.clear()
    m._DEFER_NORMAL.clear()


def test_extract_host_from_field():
    assert extract_host({"host": "Example.COM"}, "x.json") == "example.com"


def test_extract_host_from_filename():
    assert extract_host({}, "law-archive.example.com.json") == "law-archive.example.com"


def test_extract_host_missing():
    assert extract_host({}, "notes.txt") == ""


def test_session_ttl_and_append():
    start_session("1", "10")
    assert get_session("1", "10") is not None
    ok, err = append_item("1", "10", "h.test", {"main_selector": "article", "host": "h.test"}, "f.json")
    assert ok and not err
    assert len(get_session("1", "10").items) == 1
    cancel_session("1", "10")
    assert get_session("1", "10") is None


def test_try_parse_recipe_file_ok():
    body = {
        "host": "z.example",
        "main_selector": "main",
        "title_selector": "h1",
        "strip_selectors": ["nav"],
    }
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body, f)
        ok, norm, err, host = try_parse_recipe_file(p, "r.json")
        assert ok
        assert not err
        assert host == "z.example"
        assert norm.get("main_selector") == "main"


def test_try_parse_recipe_file_bad_json():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("not json")
        ok, _n, err, _h = try_parse_recipe_file(p, "r.json")
        assert not ok
        assert "JSON" in err or "json" in err.lower()


def test_max_batch_positive():
    assert max_batch() >= 1
