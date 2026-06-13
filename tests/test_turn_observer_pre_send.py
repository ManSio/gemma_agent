"""turns.jsonl: outbound_thread_guard_issues via turn.pre_send enrich."""
from __future__ import annotations

import json

from core.turn_observer import (
    append_turn_record,
    enrich_turn_record_by_trace_id,
    record_from_turn_outcome,
    record_from_turn_pre_send,
)


def test_turn_outcome_includes_outbound_thread_guard_issues() -> None:
    captured: list = []

    def _fake_append(row: dict) -> None:
        captured.append(row)

    import core.turn_observer as to

    orig = to.append_turn_record
    to.append_turn_record = _fake_append
    try:
        record_from_turn_outcome(
            {
                "user_id": "u1",
                "profile": "standard",
                "outcome": "ok",
                "outbound_thread_guard_issues": ["thread_followup_agent_meta"],
            }
        )
    finally:
        to.append_turn_record = orig

    row = captured[-1]
    assert row.get("outbound_thread_guard_issues") == ["thread_followup_agent_meta"]
    assert "thread_followup_agent_meta" in (row.get("issues") or [])


def test_pre_send_enriches_matching_trace_id(tmp_path, monkeypatch) -> None:
    import core.turn_observer as to

    log_file = tmp_path / "turns.jsonl"
    monkeypatch.setenv("GEMMA_TURNS_LOG_PATH", str(log_file))
    append_turn_record(
        {
            "ts": "2026-06-13T12:00:00+00:00",
            "trace_id": "trace-abc",
            "user_id": "u1",
            "outcome": "ok",
            "issues": [],
        }
    )
    ok = enrich_turn_record_by_trace_id(
        "trace-abc",
        {"outbound_thread_guard_issues": ["thread_followup_agent_meta"]},
    )
    assert ok is True
    row = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[0])
    assert row.get("outbound_thread_guard_issues") == ["thread_followup_agent_meta"]
    assert "thread_followup_agent_meta" in row.get("issues", [])


def test_pre_send_fallback_row_when_trace_missing(tmp_path, monkeypatch) -> None:
    import core.turn_observer as to

    log_file = tmp_path / "turns.jsonl"
    monkeypatch.setenv("GEMMA_TURNS_LOG_PATH", str(log_file))
    record_from_turn_pre_send(
        {
            "trace_id": "missing-trace",
            "user_id": "u9",
            "outbound_thread_guard_issues": ["thread_followup_no_overlap"],
        }
    )
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row.get("type") == "pre_send"
    assert row.get("outbound_thread_guard_issues") == ["thread_followup_no_overlap"]
