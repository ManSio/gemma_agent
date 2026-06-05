"""Согласованность каталога команд, хендлеров и текста для мозга."""
from __future__ import annotations

import re
from pathlib import Path

from core.command_catalog import (
    CORE_COMMANDS,
    all_slash_tokens_for_brain_catalog,
    discover_aiogram_command_tokens,
    format_brain_telegram_command_catalog,
)
from core.plugin_registry import PluginRegistry


def _tokens_from_handler_files() -> set[str]:
    base = Path(__file__).resolve().parent.parent / "core" / "input_handlers"
    pat = re.compile(r'Command\(\s*"([^"]+)"')
    out: set[str] = set()
    for path in sorted(base.glob("commands_*.py")):
        raw = path.read_text(encoding="utf-8")
        for m in pat.finditer(raw):
            tok = (m.group(1) or "").strip().lower()
            if tok:
                out.add(tok)
    return out


def test_discover_aiogram_matches_handler_files():
    assert discover_aiogram_command_tokens() == _tokens_from_handler_files()


def test_brain_catalog_lists_every_handler_token():
    bag = set(all_slash_tokens_for_brain_catalog())
    for tok in discover_aiogram_command_tokens():
        assert tok in bag, f"handler Command('{tok}') missing from brain catalog union"


def test_brain_catalog_lists_core_specs():
    text = format_brain_telegram_command_catalog(None, max_chars=100_000)
    for spec in CORE_COMMANDS:
        for t in spec.all_tokens():
            assert f"/{t}" in text, f"CORE_COMMANDS token /{t} missing from brain catalog text"


def test_brain_catalog_smoke_with_empty_registry():
    reg = PluginRegistry()
    txt = format_brain_telegram_command_catalog(reg, max_chars=5000)
    assert "/help" in txt
    assert "/admin_health" in txt
    assert "/goal_run" in txt
    assert "/calc" in txt


def test_critical_agent_docstrings_mention_tool_families():
    from core.brain import constants as C

    blob = C.AGENT_INSTRUCTION + C.BRAIN_CAPABILITY_HONESTY + C.AGENT_DOMAIN_UKA_COMPACT
    for needle in (
        "ArithmeticTool",
        "UrlFetch",
        "UniversalSearch",
        "LawSearch",
        "DialogRecall",
        "UserKnowledgeArchive",
        "personal_library",
        "telegram_commands_catalog",
    ):
        assert needle in blob, f"agent prompt blob should mention {needle!r} for tooling honesty"


def test_brain_command_usage_blurb():
    from core.command_catalog import BRAIN_COMMAND_CATALOG_USAGE

    assert "/help" in BRAIN_COMMAND_CATALOG_USAGE
    assert "/goal_run" in BRAIN_COMMAND_CATALOG_USAGE
