"""Контракт единого runtime-каталога команд.

Эти тесты ловят рассинхрон между core.command_catalog и реальными runners
до выката, чтобы не получить «exclusive без обработчика».
"""
from __future__ import annotations

from core import command_catalog
from core.command_catalog import (
    CORE_COMMANDS,
    collect_full_catalog,
    find_core_spec,
    get_core_exclusive_tokens,
    get_core_runner_attrs,
    is_admin_command_pattern,
    is_orchestrator_skip_text,
    normalize_command_token,
)
from core.input_handlers import telegram_command_runners as _runners


def test_core_commands_have_unique_primary_tokens():
    seen: set[str] = set()
    for spec in CORE_COMMANDS:
        assert spec.token not in seen, f"duplicate primary token: {spec.token}"
        seen.add(spec.token)


def test_core_commands_runner_attrs_exist():
    """Если spec.runner_attr задан — функция обязана существовать в telegram_command_runners."""
    for spec in CORE_COMMANDS:
        if not spec.runner_attr:
            continue
        fn = getattr(_runners, spec.runner_attr, None)
        assert callable(fn), (
            f"core command '/{spec.token}' заявляет runner '{spec.runner_attr}',"
            " но такая функция не найдена в core.input_handlers.telegram_command_runners"
        )


def test_aliases_do_not_collide_with_other_primary_tokens():
    primaries = {spec.token for spec in CORE_COMMANDS}
    for spec in CORE_COMMANDS:
        for alias in spec.aliases:
            if alias in primaries and alias != spec.token:
                raise AssertionError(
                    f"alias '{alias}' у '/{spec.token}' конфликтует с другой основной командой"
                )


def test_normalize_and_skip_helpers():
    assert normalize_command_token("/Help@SomeBot foo") == "help"
    assert normalize_command_token("просто текст") == ""
    assert is_admin_command_pattern("admin_health") is True
    assert is_admin_command_pattern("auto_review") is True
    assert is_admin_command_pattern("explain") is False
    assert is_orchestrator_skip_text("/admin_health_json") is True
    assert is_orchestrator_skip_text("/help") is True
    assert is_orchestrator_skip_text("/explain physics test") is False


def test_runner_attrs_map_covers_inline_dispatch_minimum():
    attrs = get_core_runner_attrs()
    for must_have in ("start", "help", "system_state", "status", "id", "me"):
        assert must_have in attrs, f"toolkit missing inline runner for /{must_have}"


def test_exclusive_tokens_include_patches_and_status():
    excl = get_core_exclusive_tokens()
    for must_have in (
        "start",
        "help",
        "system_state",
        "status",
        "list_patches",
        "approve_suggested_patch",
        "dismiss_suggested_patch",
    ):
        assert must_have in excl, f"'{must_have}' should be exclusive (handled by core)"


def test_find_core_spec_supports_aliases():
    s1 = find_core_spec("system_state")
    s2 = find_core_spec("status")
    assert s1 is not None and s2 is s1


def test_collect_full_catalog_smoke():
    snapshot = collect_full_catalog(plugin_registry=None)
    assert snapshot["core_total"] == len(CORE_COMMANDS)
    assert isinstance(snapshot["plugin_commands"], list)
    assert isinstance(snapshot["collisions"], dict)
