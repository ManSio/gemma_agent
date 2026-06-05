"""Turn observer, route_risk stumble filter, user correction bus."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def test_should_record_stumble_skips_plain_clarify(monkeypatch):
    from core.route_risk_memory import should_record_stumble

    monkeypatch.delenv("ROUTE_RISK_RECORD_CLARIFY", raising=False)
    assert should_record_stumble(outcome="clarify", detail="") is False
    assert should_record_stumble(outcome="clarify", detail="math_ambiguous") is True
    assert should_record_stumble(outcome="failure", detail="") is True
    assert should_record_stumble(outcome="ok", detail="", user_feedback_negative=True) is True


def test_record_turn_includes_memory_telemetry():
    from core.turn_observer import record_from_turn_outcome, read_recent_turns

    record_from_turn_outcome(
        {
            "user_id": "mem1",
            "outcome": "ok",
            "user_excerpt": "что ещё",
            "assistant_excerpt": "ответ",
            "dialogue_slot_kind": "article_thread",
            "policy_hint_tags": ["ARTICLE_THREAD", "RECHECK_ANCHOR"],
            "policy_slot_keys": ["article_thread"],
            "correction_pending": True,
            "last_feedback_applied": ["pending_correction"],
        }
    )
    rows = read_recent_turns(limit=5, user_id="mem1")
    assert rows
    last = rows[-1]
    assert last.get("dialogue_slot_kind") == "article_thread"
    assert "ARTICLE_THREAD" in (last.get("policy_hint_tags") or [])
    assert last.get("correction_pending") is True


def test_record_turn_includes_latency():
    from core.turn_observer import record_from_turn_outcome, read_recent_turns

    record_from_turn_outcome(
        {
            "user_id": "9",
            "outcome": "ok",
            "latency_ms": 4200,
            "profile": "standard",
            "module": "chat_orchestrator",
            "user_excerpt": "test",
            "assistant_excerpt": "ответ",
        }
    )
    rows = read_recent_turns(limit=3, user_id="9")
    assert rows and rows[-1].get("latency_ms") == 4200


def test_format_turns_admin_html_import():
    from core.turn_observer import format_turns_admin_html

    html = format_turns_admin_html(
        [{"ts": "2026-05-21T12:00:00", "outcome": "ok", "user_excerpt": "привет", "assistant_excerpt": "здравствуй", "issues": []}],
        title="test",
    )
    assert "привет" in html
    assert "ok" in html


def test_detect_issues_negative_feedback():
    from core.turn_observer import detect_issues

    issues = detect_issues(
        outcome="ok",
        user_feedback_negative=True,
        user_feedback_positive=False,
        assistant_excerpt="hello",
        detail="",
    )
    assert "user_feedback_negative" in issues


def test_read_recent_turns_issues_only(tmp_path, monkeypatch):
    from core import turn_observer as to

    log = tmp_path / "turns.jsonl"
    monkeypatch.setenv("GEMMA_TURNS_LOG_PATH", str(log))
    to.append_turn_record({"ts": "1", "user_id": "1", "outcome": "ok", "issues": []})
    to.append_turn_record({"ts": "2", "user_id": "1", "outcome": "fallback", "issues": ["outcome_fallback"]})
    rows = to.read_recent_turns(limit=10, issues_only=True)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "fallback"


def test_turn_observer_log_path_resolves_relative(tmp_path, monkeypatch):
    from core import turn_observer as to

    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("GEMMA_TURNS_LOG_PATH", "data/runtime/turns.jsonl")
    assert to.log_path() == (tmp_path / "data/runtime/turns.jsonl").resolve()


def test_turn_observer_append(tmp_path, monkeypatch):
    from core import turn_observer as to

    log = tmp_path / "turns.jsonl"
    monkeypatch.setenv("GEMMA_TURNS_LOG_PATH", str(log))
    monkeypatch.setenv("TURN_OBSERVER_ENABLED", "true")
    to.record_from_turn_outcome(
        {
            "user_id": "1",
            "intent": "general",
            "module": "chat_orchestrator",
            "outcome": "ok",
            "user_excerpt": "привет",
            "assistant_excerpt": "здравствуй",
            "fp": "abc",
        }
    )
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["user_id"] == "1"
    assert row["intent"] == "general"


def test_build_operator_corrections_hint_ephemeral(monkeypatch):
    from core.user_correction_bus import build_operator_corrections_hint

    monkeypatch.setenv("BRAIN_OPERATOR_CORRECTIONS_IN_HINT", "true")
    h = build_operator_corrections_hint(
        {"ephemeral_lessons_brain_addon": "Временные правки:\n- не калькулятор"}
    )
    assert "не калькулятор" in h


def test_negative_rating_lesson_instruction_translate():
    from core.user_correction_bus import _negative_rating_lesson_instruction

    t = _negative_rating_lesson_instruction(
        user_text="переведи на английский: привет",
        intent="general",
        module="chat",
        correction_text="",
    )
    assert "перевод" in t.lower()
