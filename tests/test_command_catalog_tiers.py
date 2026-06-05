"""Два уровня каталога команд для мозга: minimal vs full."""
from __future__ import annotations

from core.command_catalog import (
    all_slash_tokens_for_brain_catalog,
    format_brain_telegram_command_catalog,
)
from core.plugin_registry import PluginRegistry


def test_minimal_catalog_is_shorter_than_full():
    reg = PluginRegistry()
    mn = format_brain_telegram_command_catalog(reg, tier="minimal", max_chars=50_000)
    fl = format_brain_telegram_command_catalog(reg, tier="full", max_chars=50_000)
    assert len(mn) < len(fl)
    assert "/help" in mn
    assert "/admin_health" not in mn or "Администрирование" in mn


def test_minimal_only_includes_known_frequent_tokens():
    reg = PluginRegistry()
    mn = format_brain_telegram_command_catalog(reg, tier="minimal")
    full_list = all_slash_tokens_for_brain_catalog()
    for line in mn.splitlines():
        line = line.strip()
        if line.startswith("/") and "→" not in line:
            tok = line[1:].split()[0].lower()
            assert tok in full_list, f"minimal line references unknown token {tok}"
