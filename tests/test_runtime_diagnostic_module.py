import asyncio

import pytest

from core import runtime_diagnostic_module as rd


def test_collect_without_orchestrator() -> None:
    rd.set_orchestrator_for_runtime_diagnostic(None)
    mod = rd.RuntimeDiagnosticModule()
    out = asyncio.run(mod.collect_diagnostic_bundle())
    assert out.get("error") == "no_orchestrator"


def test_compact_bundle_may_omit_heavy_sections() -> None:
    raw = {"code_cartography": {"files": list(range(40))}, "runtime_errors_recent": [{"n": i} for i in range(80)]}
    sz = rd._json_size(raw)
    out = rd._compact_bundle(dict(raw), max_chars=max(200, sz // 3))
    assert out.get("_compacted_for_brain_tool") is True
    assert isinstance(out.get("code_cartography"), dict)
    assert out["code_cartography"].get("_omitted") is True or len(out.get("runtime_errors_recent") or []) < 80


def test_max_json_chars_env(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DIAG_TOOL_MAX_JSON_CHARS", "25000")
    assert rd._max_json_chars() == 25000
