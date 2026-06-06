"""Сборка plugin_manifest_prompts из module.json (loaded_modules)."""

from __future__ import annotations

from types import SimpleNamespace

from core.plugin_prompts import format_plugin_prompts_for_brain


def test_format_plugin_prompts_empty_when_no_loaded():
    reg = SimpleNamespace(loaded_modules={})
    assert format_plugin_prompts_for_brain(reg) == ""


def test_format_plugin_prompts_joins_manifest_keys(monkeypatch):
    monkeypatch.setenv("BRAIN_PLUGIN_MANIFEST_PROMPTS_MAX_CHARS", "10000")
    m1 = SimpleNamespace(
        name="school_assistant",
        prompts={"system": "Учебный помощник.", "extra": "Ещё строка."},
    )
    m2 = SimpleNamespace(name="echo", prompts={})
    inst1 = SimpleNamespace(manifest=m1)
    inst2 = SimpleNamespace(manifest=m2)
    reg = SimpleNamespace(loaded_modules={"a": inst1, "b": inst2})
    out = format_plugin_prompts_for_brain(reg)
    assert "### plugin:school_assistant" in out
    assert "Учебный помощник." in out
    assert "Ещё строка." in out
    assert "echo" not in out


def test_format_plugin_prompts_module_filter(monkeypatch):
    monkeypatch.setenv("BRAIN_PLUGIN_MANIFEST_PROMPTS_MAX_CHARS", "10000")
    m1 = SimpleNamespace(name="keep", prompts={"system": "A"})
    m2 = SimpleNamespace(name="drop", prompts={"system": "B"})
    reg = SimpleNamespace(
        loaded_modules={
            "keep": SimpleNamespace(manifest=m1),
            "drop": SimpleNamespace(manifest=m2),
        }
    )
    out = format_plugin_prompts_for_brain(reg, module_filter=lambda n: n == "keep")
    assert "A" in out
    assert "B" not in out


def test_format_plugin_prompts_respects_max_chars(monkeypatch):
    monkeypatch.setenv("BRAIN_PLUGIN_MANIFEST_PROMPTS_MAX_CHARS", "80")
    long_text = "x" * 200
    m = SimpleNamespace(name="p1", prompts={"system": long_text})
    reg = SimpleNamespace(loaded_modules={"p1": SimpleNamespace(manifest=m)})
    out = format_plugin_prompts_for_brain(reg)
    assert len(out) <= 90
    assert "…" in out or len(out) < len(long_text)
