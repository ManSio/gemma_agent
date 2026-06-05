"""HEURISTIC_SHORTCUTS_FREEZE — новые rule id из local не подхватываются."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.heuristic_shortcuts_registry import load_shortcut_rules, registry_reload


class TestHeuristicShortcutsFreeze(unittest.TestCase):
    def tearDown(self) -> None:
        registry_reload()

    def test_freeze_ignores_new_local_rule_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            cfg.mkdir()
            base = {
                "rules": [{"id": "news_direct", "domain": "news", "requires": []}],
            }
            (cfg / "heuristic_shortcuts.json").write_text(
                json.dumps(base), encoding="utf-8"
            )
            local = {
                "rules": [{"id": "brand_new_shortcut", "domain": "news", "requires": []}],
            }
            (cfg / "heuristic_shortcuts.local.json").write_text(
                json.dumps(local), encoding="utf-8"
            )
            registry_reload()
            with patch.dict(
                os.environ,
                {
                    "GEMMA_PROJECT_ROOT": td,
                    "HEURISTIC_SHORTCUTS_FREEZE": "true",
                },
                clear=False,
            ):
                registry_reload()
                rules = load_shortcut_rules()
            self.assertIn("news_direct", rules)
            self.assertNotIn("brand_new_shortcut", rules)


if __name__ == "__main__":
    unittest.main()
