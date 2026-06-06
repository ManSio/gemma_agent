from pathlib import Path

import pytest

from core import ephemeral_autolearn as al
from core.ephemeral_lessons import load_document


def test_extract_remember_phrase():
    d = al.extract_lesson_draft(
        "запомни: для ссылок t.me/+ не предлагай /calc, это инвайты",
        "",
    )
    assert d is not None
    assert d.get("weight") == 2
    assert "t.me" in (d.get("trigger") or "").lower() or "t.me" in (d.get("instruction") or "").lower()


def test_extract_complaint_calc():
    d = al.extract_lesson_draft("убери калькулятор на этих ссылках t.me", "")
    assert d is not None
    assert d.get("weight") >= 1


def test_personal_fact_not_lesson():
    assert al.extract_lesson_draft("запомни как меня зовут Миша", "") is None


def test_process_promotes_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_AUTOLEARN_STRIKES_TRUSTED", "2")
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_path / "el.json"))
    monkeypatch.setenv("EPHEMERAL_AUTOLEARN_MAX_PROMOTIONS_PER_DAY", "99")
    rec: dict = {"ephemeral_autolearn": {"buckets": {}}}
    msg = (
        "ты опять не так отвечаешь когда я про бота спрашиваю разбор диалога в переписке и повторяешь ошибку"
    )
    r1 = al.process_turn_for_autolearn(
        rec,
        msg,
        "ok",
        user_id="1",
        group_id=None,
        telegram_is_admin=True,
    )
    assert r1 and not r1.get("promoted")
    r2 = al.process_turn_for_autolearn(
        rec,
        msg,
        "sorry",
        user_id="1",
        group_id=None,
        telegram_is_admin=True,
    )
    assert r2 and r2.get("promoted")
    doc = load_document()
    assert doc.get("lessons")


def test_process_untrusted_queues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_AUTOLEARN_STRIKES_UNTRUSTED", "999")
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_path / "el.json"))
    monkeypatch.setenv("EPHEMERAL_PENDING_PATH", str(tmp_path / "pend.json"))
    rec: dict = {"ephemeral_autolearn": {"buckets": {}}}
    r1 = al.process_turn_for_autolearn(
        rec,
        "не предлагай /calc на t.me/+ приглашениях",
        "",
        user_id="99",
        group_id=None,
        telegram_is_admin=False,
    )
    assert r1 and r1.get("note") == "queued_non_trusted"
    assert al.pending_list()


def test_pending_auto_promote_two_distinct_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EPHEMERAL_LESSONS_PATH", str(tmp_path / "el.json"))
    monkeypatch.setenv("EPHEMERAL_PENDING_PATH", str(tmp_path / "pend.json"))
    monkeypatch.setenv("EPHEMERAL_PENDING_AUTO_PROMOTE_USERS", "2")
    monkeypatch.setenv("EPHEMERAL_AUTOLEARN_MAX_PROMOTIONS_PER_DAY", "99")
    draft = al.extract_lesson_draft("не предлагай /calc на t.me/+ приглашениях", "")
    assert draft
    r1 = al.pending_append(draft, from_user_id="10", group_id=None)
    assert r1.get("id")
    r2 = al.pending_append(draft, from_user_id="20", group_id=None)
    assert r2 and r2.get("auto_promoted") and r2.get("lesson", {}).get("id")
    doc = load_document()
    assert any(x.get("meta", {}).get("source") == "pending_auto_distinct_users" for x in doc.get("lessons") or [])
