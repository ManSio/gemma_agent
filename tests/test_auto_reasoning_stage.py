import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.brain.auto_reasoning_plugins import (
    auto_reasoning_plugins_report,
    extract_auto_reasoning_gates,
)


def test_auto_reasoning_plugins_report_disabled(monkeypatch):
    monkeypatch.setenv("BRAIN_AUTO_REASONING_PLUGINS", "false")

    async def _run():
        rep = await auto_reasoning_plugins_report("any text")
        assert rep == ""

    asyncio.run(_run())


def test_auto_reasoning_plugins_report_enabled_public(monkeypatch):
    """Public build: tier C plugins removed — report empty even when flag on."""
    monkeypatch.setenv("BRAIN_AUTO_REASONING_PLUGINS", "true")

    async def _run():
        rep = await auto_reasoning_plugins_report("INIT->READY must:ready forbid:error")
        assert rep == ""

    asyncio.run(_run())


def test_extract_auto_reasoning_gates():
    rep = (
        "AUTO_REASONING_PLUGIN_REPORT:\n"
        '{ "auto_reasoning_plugins": { "gates": { "error_memory_hits": 2, "instruction_missed_steps": 1 } } }'
    )
    g = extract_auto_reasoning_gates(rep)
    assert g["error_memory_hits"] == 2
    assert g["instruction_missed_steps"] == 1
