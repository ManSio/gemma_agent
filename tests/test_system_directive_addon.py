import os
from pathlib import Path

from core.system_directive_addon import load_system_directive_brain_addon, system_directive_addon_path


def test_addon_path_respects_env(tmp_path, monkeypatch):
    p = tmp_path / "x.txt"
    p.write_text("hello addon", encoding="utf-8")
    monkeypatch.setenv("SYSTEM_DIRECTIVE_ADDON_PATH", str(p))
    assert system_directive_addon_path() == p.resolve()
    assert load_system_directive_brain_addon() == "hello addon"


def test_addon_missing_is_empty(monkeypatch):
    monkeypatch.setenv("SYSTEM_DIRECTIVE_ADDON_PATH", str(Path("/nonexistent/gemma_no_such_file.txt")))
    assert load_system_directive_brain_addon() == ""
