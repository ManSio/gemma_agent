import json
import os
from pathlib import Path

import pytest

from core.memory_runtime_report import build_memory_insight_payload, format_memory_insight_plain


def test_memory_insight_reads_tails(monkeypatch, tmp_path: Path) -> None:
    sp = tmp_path / "strategy_paths.jsonl"
    rr = tmp_path / "route_risk.jsonl"
    ex = tmp_path / "experience.jsonl"
    sp.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00+00:00",
                "fp": "abc",
                "intent": "chat",
                "module": "chat_orchestrator",
                "task_tier": "deep",
                "path_style": "long",
                "steps_summary": "a → b",
                "assistant_excerpt": "hello",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rr.write_text(
        json.dumps(
            {"fp": "def", "intent": "x", "module": "m", "outcome": "fallback", "detail": "reason"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    ex.write_text(
        json.dumps(
            {
                "ts": "2026-01-02T00:00:00+00:00",
                "fp": "ghi",
                "intent": "y",
                "module": "m2",
                "planner_reason": "because",
                "user_excerpt": "u",
                "assistant_excerpt": "a",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEMMA_STRATEGY_PATH", str(sp))
    monkeypatch.setenv("GEMMA_ROUTE_RISK_PATH", str(rr))
    monkeypatch.setenv("GEMMA_EXPERIENCE_PATH", str(ex))
    monkeypatch.setenv("STRATEGY_PATH_MEMORY_ENABLED", "true")
    monkeypatch.setenv("ROUTE_RISK_MEMORY_ENABLED", "true")
    monkeypatch.setenv("EXPERIENCE_MEMORY_ENABLED", "true")

    p = build_memory_insight_payload(limit_per_file=5, user_id=None, group_id=None)
    assert len(p["strategy_paths_tail"]) == 1
    assert p["strategy_paths_tail"][0].get("steps_summary") == "a → b"
    assert len(p["route_risk_tail"]) == 1
    assert p["route_risk_tail"][0].get("outcome") == "fallback"
    assert len(p["experience_tail"]) == 1

    plain = format_memory_insight_plain(p)
    assert "a → b" in plain
    assert "fallback" in plain
    assert "because" in plain
    assert "Как пишется в рантайм" in plain
    assert ".199369" not in plain
    assert "01.01.2026" in plain


def test_memory_insight_respects_disable(monkeypatch, tmp_path: Path) -> None:
    sp = tmp_path / "s.jsonl"
    sp.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("GEMMA_STRATEGY_PATH", str(sp))
    monkeypatch.setenv("STRATEGY_PATH_MEMORY_ENABLED", "false")
    p = build_memory_insight_payload(limit_per_file=3, user_id=None, group_id=None)
    assert p["strategy_paths_tail"] == []
