"""Регрессия audit P0/P1: plugin_registry shim, facts suppress, mem0 getter."""
from __future__ import annotations

from core.plugin_registry import PluginRegistry, plugin_registry, set_plugin_registry
from core.user_facts import _facts_should_suppress_confirmation


def test_plugin_registry_shim_without_bind() -> None:
    set_plugin_registry(None)
    assert plugin_registry.disable_module("echo") is False
    assert plugin_registry.enable_module("echo") is False


def test_plugin_registry_shim_with_registry(tmp_path) -> None:
    mods = tmp_path / "modules"
    mods.mkdir()
    reg = PluginRegistry(str(mods))
    set_plugin_registry(reg)
    assert plugin_registry.disable_module("nonexistent_module_xyz") is False


def test_facts_suppress_not_rss() -> None:
    assert _facts_should_suppress_confirmation("не rss") is True
    assert _facts_should_suppress_confirmation("привет") is False


def test_get_memory_after_configure() -> None:
    from core.brain.runtime import configure_brain_memory, get_memory
    from core.mem0_memory.mem0_module import Mem0MemoryModule

    before = get_memory()
    other = Mem0MemoryModule({"api_url": "http://127.0.0.1:1"})
    configure_brain_memory(other)
    assert get_memory() is other
    configure_brain_memory(before)
