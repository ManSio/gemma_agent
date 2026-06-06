import pytest

from core.llm_task_outline import outline_enabled, should_run_outline


def test_outline_enabled_default_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STRATEGY_LLM_OUTLINE_ENABLED", raising=False)
    assert outline_enabled() is False


def test_should_run_outline_for_nested_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STRATEGY_LLM_OUTLINE_ENABLED", "true")
    assert should_run_outline("x", "nested") is True
    assert should_run_outline("x", "deep") is True


def test_should_run_outline_off_when_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STRATEGY_LLM_OUTLINE_ENABLED", "false")
    assert should_run_outline("x", "nested") is False
