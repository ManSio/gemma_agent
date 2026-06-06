"""Навигация /help и /admin."""
from __future__ import annotations

import unittest

from core.input_handlers.telegram_nav import (
    admin_menu_page_count,
    build_admin_menu_keyboard,
    help_hub_nav_rows,
)


class TelegramNavTests(unittest.TestCase):
    def test_admin_menu_pagination(self) -> None:
        self.assertGreaterEqual(admin_menu_page_count(), 3)
        kb = build_admin_menu_keyboard(page=1)
        flat = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("admin:dashboard", flat)
        self.assertIn("admin:menu_2", flat)

    def test_help_hub_has_user_more(self) -> None:
        rows = help_hub_nav_rows(active="main")
        flat = [b.callback_data for row in rows for b in row]
        self.assertIn("help:user_more", flat)
        self.assertIn("help:modules_1", flat)


if __name__ == "__main__":
    unittest.main()
