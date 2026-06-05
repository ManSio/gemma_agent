"""turns.jsonl: поля heuristic gate из router_route_audit."""
from __future__ import annotations

from core.turn_observer import record_from_turn_outcome


def test_turn_row_includes_heuristic_gate_fields() -> None:
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
                "router_route_audit": {
                    "router_source": "llm",
                    "heuristic_gate": [
                        {
                            "shortcut_rule_id": "profile_math_substring",
                            "gate_verdict": "blocked",
                            "gate_block_reason": "prose_narrative",
                            "topic_current": "finance",
                        }
                    ],
                },
            }
        )
    finally:
        to.append_turn_record = orig

    assert captured
    row = captured[-1]
    assert row.get("shortcut_rule_id") == "profile_math_substring"
    assert row.get("gate_verdict") == "blocked"
    assert row.get("gate_block_reason") == "prose_narrative"
    assert row.get("topic_current") == "finance"


def test_turn_row_topic_tracking_from_payload() -> None:
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
                "topic_tracking": {"current": "кошка Мурка", "snippet": "как зовут кошку"},
            }
        )
    finally:
        to.append_turn_record = orig

    row = captured[-1]
    assert row.get("topic_current") == "кошка Мурка"
    assert "кошку" in str(row.get("topic_snippet") or "")


def test_admin_turns_html_shows_topic_and_gate() -> None:
    from core.turn_observer import format_turns_admin_html

    html = format_turns_admin_html(
        [
            {
                "ts": "2026-05-23T12:00:00+00:00",
                "outcome": "ok",
                "profile": "standard",
                "user_excerpt": "тест",
                "assistant_excerpt": "ответ",
                "topic_current": "finance",
                "gate_verdict": "blocked",
                "shortcut_rule_id": "geo_nearby",
                "gate_block_reason": "prose_over_chars",
            }
        ]
    )
    assert "topic:" in html
    assert "finance" in html
    assert "geo_nearby" in html
