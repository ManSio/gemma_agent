"""Тесты контракта плагинов и валидации манифестов."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from core.plugin_contract import ManifestIssue, validate_manifest, validate_registry
from core.plugin_registry import ModuleManifest


class _Manifest:
    def __init__(
        self,
        *,
        name: str,
        type: str = "tool",
        commands: List = None,
        buttons: List = None,
        capabilities: List[str] = None,
    ):
        self.name = name
        self.type = type
        self.commands = commands or []
        self.buttons = buttons or []
        self.capabilities = capabilities or []
        self.pip_requirements: List[str] = []
        self.requires: List[str] = []

    def iter_command_tokens(self) -> List[str]:
        out: List[str] = []
        for c in self.commands:
            if isinstance(c, str):
                t = c.strip().lstrip("/").split("@")[0].lower()
            elif isinstance(c, dict):
                v = c.get("trigger") or c.get("command") or c.get("name") or ""
                t = str(v).strip().lstrip("/").split("@")[0].lower()
            else:
                t = ""
            if t:
                out.append(t)
        return out


def _codes(issues: List[ManifestIssue]) -> List[str]:
    return [i.code for i in issues]


def test_validate_manifest_detects_collision_with_core():
    m = _Manifest(name="rogue", commands=["/help"])
    issues = validate_manifest(m)
    assert "command_collides_with_core" in _codes(issues)


def test_validate_manifest_detects_duplicate_inside_plugin():
    m = _Manifest(name="dup", commands=["/foo", "/foo"])
    issues = validate_manifest(m)
    assert "duplicate_command_in_plugin" in _codes(issues)


def test_validate_manifest_detects_empty_command():
    m = _Manifest(name="empty_cmd", commands=["", {"trigger": "  "}])
    issues = validate_manifest(m)
    assert "empty_command" in _codes(issues)


def test_validate_manifest_warns_tool_without_commands_and_capabilities():
    m = _Manifest(name="lonely", type="tool")
    issues = validate_manifest(m)
    assert "tool_without_capabilities_or_commands" in _codes(issues)


def test_validate_manifest_detects_cross_plugin_conflict():
    m = _Manifest(name="b", commands=["/x"])
    issues = validate_manifest(m, other_plugin_tokens={"a": ["x"]})
    assert "command_collides_with_plugin" in _codes(issues)


def test_validate_manifest_button_warnings():
    m = _Manifest(
        name="ui",
        type="ui",
        buttons=[{"foo": "bar"}, "not a dict", {"text": "ok"}],
    )
    issues = validate_manifest(m)
    codes = _codes(issues)
    assert "button_no_text" in codes
    assert "button_no_action" in codes
    assert "button_not_dict" in codes


class _FakeReg:
    def __init__(self, plugins: dict):
        self.loaded_modules = plugins


def test_validate_registry_aggregates_collisions_and_summary():
    reg = _FakeReg(
        {
            "alpha": _ModWrap(_Manifest(name="alpha", commands=["/foo"])),
            "beta": _ModWrap(_Manifest(name="beta", commands=["/foo", "/help"])),
        }
    )
    snap = validate_registry(reg)
    assert snap["total"] == 2
    # Конфликт /foo (между плагинами) и /help (с ядром).
    assert "foo" in snap["collisions"]
    assert "help" in snap["collisions"]
    assert snap["with_errors"] >= 1


class _ModWrap:
    def __init__(self, manifest):
        self.manifest = manifest


def test_math_module_json_no_core_calc_collision():
    """Плагин math не должен объявлять /calc — токен зарезервирован ядром (release_guard smoke)."""
    p = Path(__file__).resolve().parents[1] / "modules" / "math" / "module.json"
    if not p.is_file():
        import pytest

        pytest.skip("math module not shipped in public build")
    manifest = ModuleManifest.model_validate(json.loads(p.read_text(encoding="utf-8")))
    issues = validate_manifest(manifest)
    errors = [i for i in issues if i.severity == "error"]
    assert not errors, [i.message for i in errors]
    tokens = set(manifest.iter_command_tokens() or [])
    assert "calc" not in tokens
    assert "math_calc" in tokens
