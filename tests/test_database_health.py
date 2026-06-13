"""Database health helper tests."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.database import check_database_health


class TestDatabaseHealth(unittest.TestCase):
    def test_ok_when_select_succeeds(self) -> None:
        conn = MagicMock()
        with patch("core.database.engine.connect") as connect:
            connect.return_value.__enter__.return_value = conn
            out = check_database_health()
        self.assertTrue(out.get("ok"))

    def test_fail_when_select_raises(self) -> None:
        with patch("core.database.engine.connect", side_effect=RuntimeError("db down")):
            out = check_database_health()
        self.assertFalse(out.get("ok"))
        self.assertIn("db down", out.get("error", ""))
