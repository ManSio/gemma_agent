"""SelfProgramming.analyze_system должен использовать реестр при TOOL_CALL без plugin_registry."""
from __future__ import annotations

import asyncio
from typing import List

from core.models import ModuleState
from core.self_programming import SelfProgrammingModule


class _FakeReg:
    def get_module_states(self) -> List[ModuleState]:
        return [
            ModuleState(name="echo", type="tool", status="healthy"),
        ]


def test_analyze_system_uses_instance_registry_when_arg_none() -> None:
    m = SelfProgrammingModule(plugin_registry=_FakeReg())
    out = asyncio.run(m.analyze_system(plugin_registry=None))
    assert out.get("plugin_registry_attached") is True
    assert out.get("module_count") == 1
    mods = out.get("modules") or []
    assert len(mods) == 1
    assert mods[0].get("name") == "echo"
    assert mods[0].get("status") == "healthy"


def test_detect_issues_accepts_serialized_modules() -> None:
    m = SelfProgrammingModule(plugin_registry=_FakeReg())
    rep = asyncio.run(m.analyze_system())
    issues = asyncio.run(m.detect_issues(rep))
    assert isinstance(issues, list)
    assert not any(i.get("type") == "module_failed" for i in issues)

    broken_rep = {
        "modules": [{"name": "x", "status": "failed", "type": "tool"}],
        "library_statuses": {"lib1": "broken"},
    }
    issues2 = asyncio.run(m.detect_issues(broken_rep))
    kinds = {i.get("type") for i in issues2}
    assert "module_failed" in kinds
    assert "library_broken" in kinds


def test_detect_issues_without_report_runs_analyze() -> None:
    m = SelfProgrammingModule(plugin_registry=_FakeReg())
    issues = asyncio.run(m.detect_issues())
    assert isinstance(issues, list)
    assert not any(i.get("type") == "module_failed" for i in issues)
