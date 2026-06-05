from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from core.goal_plan_validate import (
    auto_append_answer,
    try_append_final_answer,
    validate_goal_plan,
    validate_with_optional_fix,
    validator_enabled,
)


def _row(kind: str, tool: str = "", note: str = "n", args: Optional[Dict[str, Any]] = None):
    return {
        "kind": kind,
        "tool": tool,
        "note": note,
        "args": args if args is not None else {},
    }


def test_validate_ok():
    plan = [_row("tool", "A.b", "do", {"x": 1}), _row("answer", "", "sum")]
    assert validate_goal_plan(plan, frozenset({"A.b"})) == []


def test_validate_last_not_answer():
    plan = [_row("tool", "A.b", "x"), _row("tool", "A.b", "y")]
    errs = validate_goal_plan(plan, frozenset({"A.b"}))
    assert any("answer" in e for e in errs)


def test_validate_unknown_tool():
    plan = [_row("tool", "Bad.Tool", "x"), _row("answer", "", "y")]
    errs = validate_goal_plan(plan, frozenset({"A.b"}))
    assert any("каталог" in e for e in errs)


def test_validate_cycle_same_sig():
    plan = [
        _row("tool", "A.b", "1", {"q": 1}),
        _row("tool", "A.b", "2", {"q": 1}),
        _row("answer", "", "3"),
    ]
    errs = validate_goal_plan(plan, frozenset({"A.b"}))
    assert any("цикл" in e or "повтор" in e for e in errs)


def test_auto_append(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_PLAN_AUTO_APPEND_ANSWER", "true")
    assert auto_append_answer() is True
    plan = [_row("tool", "A.b", "only tool", {})]
    errs = validate_with_optional_fix(plan, frozenset({"A.b"}))
    assert errs == []
    assert str(plan[-1].get("kind")) == "answer"


def test_validator_disabled(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_PLAN_VALIDATOR", "false")
    assert validator_enabled() is False
    bad = [_row("tool", "X", "n")]
    assert validate_with_optional_fix(bad, frozenset()) == []


def test_try_append_id():
    plan = [
        {"id": 0, "kind": "tool", "tool": "T", "note": "a", "args": {}},
    ]
    assert try_append_final_answer(plan) is True
    assert plan[-1]["id"] == 1
