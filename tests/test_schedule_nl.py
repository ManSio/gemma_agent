import os
from pathlib import Path

from core.schedule_nl import extract_schedule_label, parse_weekly_schedule, try_schedule_weekly_nl


def test_parse_weekly_monday(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    parsed = parse_weekly_schedule("каждый понедельник в 09:30 позвонить маме", user_id="u1")
    assert parsed is not None
    dows, h, mi = parsed
    assert 0 in dows
    assert (h, mi) == (9, 30)


def test_schedule_weekly_nl(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SCHEDULE_NL_ENABLED", "true")
    res = try_schedule_weekly_nl("u_w", "каждый вторник в 18:00 тренировка")
    assert res is not None
    assert res.get("ok") is True
    assert "еженедельн" in res.get("reply", "").lower()


def test_daily_schedule_label_and_reply(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SCHEDULE_NL_ENABLED", "true")
    text = "Ты можешь каждый день в 20:00 собирать информацию по актуальным кодам"
    label = extract_schedule_label(text)
    assert "собирать" in label.lower()
    assert "каждый день" not in label.lower()
    res = try_schedule_weekly_nl("u_d", text)
    assert res is not None and res.get("ok") is True
    assert "ежеднев" in res.get("reply", "").lower()
