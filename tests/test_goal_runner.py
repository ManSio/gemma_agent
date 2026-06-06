from __future__ import annotations

import asyncio
import time

import pytest

from core.goal_runner import (
    STATE_KEY,
    _auto_run_enabled,
    _auto_start_smart_llm_enabled,
    _goal_runner_llm_provider,
    _goal_runner_progress,
    _goal_runner_step_progress_text,
    _goal_runner_telegram_progress_enabled,
    _llm_classify_multistep_goal,
    _plan_rows_from_llm_steps,
    _sanitize_plan_tools,
    _weak_tool_result,
    _wall_exceeded,
    autonomous_agent,
    auto_start_from_nl,
    enabled,
    executor_mode,
    run_all_in_one,
    try_goal_runner_turn,
)
from core.models import Output
from core.brain_own_turn import planner_direct_allowed


class _FakeStore:
    def __init__(self) -> None:
        self.rec: dict = {}

    def load(self, uid, gid):
        return dict(self.rec)

    def save(self, uid, gid, rec):
        self.rec = dict(rec)

    def patch_session_task(self, uid, gid, patch):
        pass


class _FakeOrch:
    def __init__(self) -> None:
        self.behavior_store = _FakeStore()
        self.openrouter = None


@pytest.fixture(autouse=True)
def _off_by_default(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_ENABLED", "false")


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_ENABLED", "false")
    monkeypatch.delenv("GOAL_RUNNER_EXECUTOR_MODE", raising=False)
    monkeypatch.delenv("GOAL_RUNNER_ULTIMATE", raising=False)
    assert enabled() is False
    o = _FakeOrch()
    assert (
        asyncio.run(
            try_goal_runner_turn(
                orchestrator=o, user_id="1", group_id=None, user_text="/goal_run test"
            )
        )
        is None
    )


def test_cancel_clears_state(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_ENABLED", "true")
    o = _FakeOrch()
    o.behavior_store.rec[STATE_KEY] = {"status": "running"}
    out = asyncio.run(
        try_goal_runner_turn(
            orchestrator=o, user_id="1", group_id=None, user_text="/goal_cancel"
        )
    )
    assert out and isinstance(out[0], Output)
    assert STATE_KEY not in o.behavior_store.rec


def test_sanitize_unknown_tool():
    plan = [{"kind": "tool", "tool": "Nope.Fake", "note": "x"}]
    _sanitize_plan_tools(plan, frozenset({"Real.Tool"}))
    assert plan[0]["tool"] == ""
    assert "каталоге" in plan[0]["note"]


def test_wall_exceeded(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_MAX_WALL_TIME_SEC", "2")
    st = {"started_at_unix": time.time() - 10.0}
    bad, msg = _wall_exceeded(st)
    assert bad is True
    assert "лимит" in msg


def test_plan_rows_from_llm_steps():
    rows = _plan_rows_from_llm_steps(
        [{"kind": "tool", "tool": "X.y", "args": {}, "note": "n"}],
        start_id=3,
    )
    assert len(rows) == 1
    assert rows[0]["id"] == 3
    assert rows[0]["kind"] == "tool"
    assert rows[0]["tool"] == "X.y"


def test_autonomous_and_auto_run(monkeypatch):
    monkeypatch.delenv("GOAL_RUNNER_AUTONOMOUS_AGENT", raising=False)
    monkeypatch.delenv("GOAL_RUNNER_RUN_ALL_IN_ONE", raising=False)
    assert autonomous_agent() is False
    assert _auto_run_enabled() is False
    monkeypatch.setenv("GOAL_RUNNER_AUTONOMOUS_AGENT", "true")
    assert autonomous_agent() is True
    assert _auto_run_enabled() is True


def test_goal_runner_llm_provider_respects_explicit_attr():
    class _O:
        pass

    o = _O()
    o.openrouter = object()
    assert _goal_runner_llm_provider(o) is o.openrouter
    o2 = _O()
    o2.openrouter = None
    assert _goal_runner_llm_provider(o2) is None


def test_goal_runner_telegram_progress_env(monkeypatch):
    monkeypatch.delenv("GOAL_RUNNER_TELEGRAM_PROGRESS", raising=False)
    assert _goal_runner_telegram_progress_enabled() is True
    monkeypatch.setenv("GOAL_RUNNER_TELEGRAM_PROGRESS", "false")
    assert _goal_runner_telegram_progress_enabled() is False


def test_goal_runner_step_progress_text_shapes():
    plan_tool = [
        {"kind": "tool", "tool": "LawSearch", "note": "x" * 100},
        {"kind": "answer"},
    ]
    s0 = _goal_runner_step_progress_text(plan_tool, 0, plan_tool[0])
    assert "1/2" in s0 and "LawSearch" in s0 and "…" in s0
    s1 = _goal_runner_step_progress_text(plan_tool, 1, plan_tool[1])
    assert "2/2" in s1 and "финальный" in s1


def test_executor_mode_one_toggle(monkeypatch):
    monkeypatch.delenv("GOAL_RUNNER_ENABLED", raising=False)
    monkeypatch.setenv("GOAL_RUNNER_EXECUTOR_MODE", "true")
    monkeypatch.setenv("GOAL_RUNNER_AUTONOMOUS_AGENT", "false")
    monkeypatch.setenv("GOAL_RUNNER_RUN_ALL_IN_ONE", "false")
    monkeypatch.setenv("GOAL_RUNNER_AUTO_START", "false")
    monkeypatch.setenv("GOAL_RUNNER_AUTO_START_SMART", "false")
    assert enabled() is True
    assert executor_mode() is True
    assert autonomous_agent() is True
    assert run_all_in_one() is True
    assert auto_start_from_nl() is False
    assert _auto_start_smart_llm_enabled() is False
    assert _auto_run_enabled() is True


def test_planner_direct_affirmative_default_deferred(monkeypatch):
    monkeypatch.setenv("BRAIN_OWN_TURN_ENABLED", "true")
    monkeypatch.delenv("BRAIN_OWN_TURN_ALLOW_AFFIRMATIVE_SEARCH", raising=False)
    assert planner_direct_allowed("affirmative_search") is False


def test_simple_weather_not_goal_runner_steal():
    from core.brain.goal_runner_nudge import warrants_multistep_goal_text
    from core.goal_runner_types import TaskType, classify_goal_runner_need

    t = "Мне нужно узнать погоду в Минске завтра"
    assert classify_goal_runner_need(t) != TaskType.MULTISTEP_TOOL
    if warrants_multistep_goal_text(t):
        assert classify_goal_runner_need(t) in {
            TaskType.SIMPLE,
            TaskType.PURE_TEXT,
            TaskType.MULTISTEP_TEXT,
        }


def test_weak_tool_result_search_empty(monkeypatch):
    assert _weak_tool_result("LawSearch.search", {"ok": True, "results": []}) is True
    assert _weak_tool_result("LawSearch.search", {"ok": True, "results": [{"url": "x"}]}) is False
    assert _weak_tool_result("LawSearch.keyword_search", {"ok": True, "hits": []}) is True
    assert _weak_tool_result("LawSearch.keyword_search", {"ok": True, "hits": [{"id": "1"}]}) is False
    assert _weak_tool_result(
        "UniversalSearch.search",
        {"ok": True, "summary": "", "results": []},
    ) is True
    assert _weak_tool_result(
        "UniversalSearch.search",
        {"ok": True, "summary": "есть текст", "results": []},
    ) is False
    assert _weak_tool_result("DocumentCorpus.unified_search", {"ok": True, "hits": []}) is True
    assert _weak_tool_result(
        "UserKnowledgeArchive.archive_search",
        {"ok": True, "items": [], "count": 0},
    ) is True
    assert _weak_tool_result(
        "UserKnowledgeArchive.archive_search",
        {"ok": True, "items": [{"snippet": "x"}], "count": 1},
    ) is False
    assert _weak_tool_result(
        "Wikipedia.scan",
        {"ok": True, "text": "", "title": "T"},
    ) is True
    assert _weak_tool_result(
        "Wikipedia.scan",
        {"ok": True, "text": "есть извлечение", "title": "T"},
    ) is False


def test_llm_classify_multistep_goal(monkeypatch):
    class _ProvY:
        async def generate(self, *args, **kwargs):
            return {"content": "Y"}

    class _ProvN:
        async def generate(self, *args, **kwargs):
            return {"content": "N"}

    class _O:
        pass

    o_y = _O()
    o_y.openrouter = _ProvY()
    assert asyncio.run(_llm_classify_multistep_goal(o_y, "любой текст")) is True
    o_n = _O()
    o_n.openrouter = _ProvN()
    assert asyncio.run(_llm_classify_multistep_goal(o_n, "любой текст")) is False


def test_goal_runner_progress_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("GOAL_RUNNER_TELEGRAM_PROGRESS", "false")
    calls: list[tuple[str, bool]] = []

    async def fake_bp(text: str, *, force: bool = False) -> None:
        calls.append((text, force))

    monkeypatch.setattr("core.brain.vision_llm.brain_progress", fake_bp)

    asyncio.run(_goal_runner_progress("ping", force=True))
    assert calls == []


def test_goal_runner_llm_provider_fallback_without_attr(monkeypatch):
    class _Bare:
        pass

    sentinel = object()

    def _fake():
        return sentinel

    monkeypatch.setattr("core.openrouter_provider.get_openrouter_provider", _fake)
    assert _goal_runner_llm_provider(_Bare()) is sentinel
