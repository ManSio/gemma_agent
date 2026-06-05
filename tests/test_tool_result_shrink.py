"""Обрезка результата инструмента перед brain_second."""

from core.brain.tool_result_shrink import shrink_tool_result_for_second_stage


def test_shrink_tool_result_noop_when_small():
    d = {"ok": True, "x": 1}
    assert shrink_tool_result_for_second_stage("T.x", d) is d


def test_shrink_tool_result_wraps_huge(monkeypatch):
    monkeypatch.setenv("BRAIN_SECOND_TOOL_RESULT_MAX_CHARS", "100")
    huge = {"blob": "y" * 500}
    out = shrink_tool_result_for_second_stage("T.x", huge)
    assert isinstance(out, dict)
    assert out.get("_brain_second_truncated") is True
    assert "_preview" in out
