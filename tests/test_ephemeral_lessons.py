from pathlib import Path

import pytest

from core import ephemeral_lessons as el


@pytest.fixture
def tmp_lessons_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "ephemeral_lessons.json"
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(p))
    return p


def test_add_lesson_rejects_too_short_contains_trigger(tmp_lessons_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_lessons_path))
    with pytest.raises(ValueError, match="короче"):
        el.add_lesson("ab", "instruction", match_regex=False)


def test_parse_remember_patch_regex_and_force():
    t, i, rx, fg = el.parse_remember_patch("regex:foo\\d+ || explain || force_general")
    assert t == r"foo\d+"
    assert i == "explain"
    assert rx is True
    assert fg is True


def test_add_match_brain_and_force_math(tmp_lessons_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_lessons_path))
    row = el.add_lesson(
        "hello",
        "say hi back",
        match_regex=False,
        force_general_when_math_probe=False,
    )
    assert row.get("id")
    addon = el.brain_addon_for_text("well hello there")
    assert "say hi back" in addon
    assert el.force_general_when_math_probe("hello") is False

    el.add_lesson("t.me/+", "no calc", force_general_when_math_probe=True)
    assert el.force_general_when_math_probe("join https://t.me/+abc") is True


def test_export_includes_operator_rules_keys(tmp_lessons_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_lessons_path))
    el.add_lesson("xyz", "do y")
    out = el.export_for_cursor()
    assert "markdown_for_cursor" in out
    assert "ephemeral_lessons" in out
    assert "operator_rules" in out
    assert "do y" in out["markdown_for_cursor"]


def test_deactivate(tmp_lessons_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_lessons_path))
    row = el.add_lesson("zzz", "inst")
    lid = row["id"]
    assert el.match_lessons("azzzb")
    assert el.deactivate_lesson(lid) is True
    assert not el.match_lessons("zzz")


def test_deactivate_legacy_generic_rating_lessons(tmp_lessons_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_lessons_path))
    el.add_lesson("почему так произошло", "исправь подход", meta={"source": "rating"})
    el.add_lesson(
        "земля круглая",
        "physics",
        meta={"anchor_user_q": "Почему земля круглая?", "source": "rating"},
    )
    n = el.deactivate_legacy_generic_rating_lessons()
    assert n == 1
    assert not el.match_lessons("почему так произошло?")
    assert el.match_lessons("земля круглая и как")
