"""Меню setMyCommands: состав списка без запуска Telegram."""
from __future__ import annotations

import unittest
from unittest import mock

from core.telegram_bot_menu import build_bot_menu_commands, _plugin_menu_entries


class _Reg:
    def __init__(self):
        self.loaded_modules = {}


class TestTelegramBotMenu(unittest.TestCase):
    def test_includes_admin_after_help_order(self):
        cmds = build_bot_menu_commands(_Reg())
        tokens = [c.command for c in cmds]
        self.assertIn("admin", tokens)
        self.assertLess(tokens.index("help"), tokens.index("admin"))

    def test_no_duplicate_commands(self):
        cmds = build_bot_menu_commands(_Reg())
        tokens = [c.command for c in cmds]
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_respects_cap(self):
        big = _Reg()

        class _M:
            manifest = mock.Mock(commands=[{"trigger": f"/c{i}", "description": f"d{i}"} for i in range(200)])

        big.loaded_modules = {f"p{i}": _M() for i in range(200)}
        cmds = build_bot_menu_commands(big)
        self.assertLessEqual(len(cmds), 100)

    def test_plugin_menu_uses_russian_override_for_known_token(self):
        reg = _Reg()

        class _M:
            manifest = mock.Mock(
                commands=[{"trigger": "/local_math", "description": "Run local arithmetic"}]
            )

        reg.loaded_modules = {"math": _M()}
        cmds = build_bot_menu_commands(reg)
        by_cmd = {c.command: c.description for c in cmds}
        self.assertIn("Локальная арифметика", by_cmd.get("local_math", ""))

    def test_description_ru_takes_priority_over_override(self):
        reg = _Reg()

        class _M:
            manifest = mock.Mock(
                commands=[
                    {
                        "trigger": "/local_math",
                        "description": "Run local arithmetic",
                        "description_ru": "Свой текст для меню",
                    }
                ]
            )

        reg.loaded_modules = {"math": _M()}
        cmds = build_bot_menu_commands(reg)
        by_cmd = {c.command: c.description for c in cmds}
        self.assertIn("Свой текст для меню", by_cmd.get("local_math", ""))

    def test_plugin_token_with_spaces_or_angle_brackets_is_stripped(self):
        """Команды с пробелами/<> обрезаются до первого слова — setMyCommands не ругается."""
        reg = _Reg()

        class _M:
            manifest = mock.Mock(
                commands=[
                    {"trigger": "/teacher_mode <role>", "description": "Учитель"},
                    {"trigger": "/set_city Moscow", "description": "Город"},
                ]
            )

        reg.loaded_modules = {"m": _M()}
        cmds = build_bot_menu_commands(reg)
        tokens = [c.command for c in cmds]
        self.assertIn("teacher_mode", tokens)
        self.assertIn("set_city", tokens)
        self.assertNotIn("teacher_mode <role>", tokens)
