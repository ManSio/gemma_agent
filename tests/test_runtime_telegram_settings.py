"""Переключатели админки → admin_telegram_settings.json приоритет над env."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.runtime_telegram_settings import (
    TOGGLE_DEFS,
    effective_bool,
    set_override,
)


class RuntimeTelegramSettingsTests(unittest.TestCase):
    def test_strategic_lenses_in_toggle_defs(self):
        keys = {env for _, env, _, _ in TOGGLE_DEFS}
        self.assertIn("STRATEGIC_LENSES_HINT_ENABLED", keys)
        self.assertIn("LOOKAHEAD_PLANNER_ENABLED", keys)

    def test_effective_bool_store_overrides_env(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "admin_telegram_settings.json")
            with patch("core.runtime_telegram_settings._store_path", return_value=__import__("pathlib").Path(path)):
                from core import runtime_telegram_settings as rts

                rts._invalidate_cache()
                os.environ["STRATEGIC_LENSES_HINT_ENABLED"] = "1"
                self.assertTrue(effective_bool("STRATEGIC_LENSES_HINT_ENABLED", default=False))
                set_override("STRATEGIC_LENSES_HINT_ENABLED", False)
                rts._invalidate_cache()
                self.assertFalse(effective_bool("STRATEGIC_LENSES_HINT_ENABLED", default=True))


if __name__ == "__main__":
    unittest.main()
