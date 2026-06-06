"""turn_resolver не должен быть в прод-пути orchestrator."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class TestArchitectureTurnResolverGuard(unittest.TestCase):
    def test_orchestrator_does_not_import_turn_resolver(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for rel in ("core/orchestrator.py", "core/brain/pipeline.py", "core/brain_own_turn.py"):
            src = (root / rel).read_text(encoding="utf-8")
            tree = ast.parse(src)
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imports_from = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertNotIn("core.turn_resolver", imports | imports_from, rel)
            self.assertNotIn("turn_resolver", src, rel)

    def test_disabled_resolver_does_not_allow_all_shortcuts(self) -> None:
        from core.turn_resolver import TurnVerdict, plan_shortcut_enabled, turn_resolver_enabled

        self.assertFalse(turn_resolver_enabled())
        v = TurnVerdict(primary="brain")
        self.assertFalse(v.allows("weather_direct"))
        self.assertFalse(plan_shortcut_enabled(v, "weather_direct"))


if __name__ == "__main__":
    unittest.main()
