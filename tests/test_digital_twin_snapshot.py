"""Сводка профиля для агента."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class DigitalTwinSnapshotTests(unittest.TestCase):
    def test_user_snapshot_for_agent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "twins.json")
            with patch.dict("os.environ", {"DIGITAL_TWINS_PATH": path}):
                from core.digital_twin import DigitalTwinModule

                mod = DigitalTwinModule(storage_path=path)
                mod.twins["1"] = {
                    "learning_profile": {
                        "interests": ["тест"],
                        "goals": [],
                        "last_updated": "2026-01-01",
                    }
                }
                mod._save()

                snap = mod.user_snapshot_for_agent("1", "")
                self.assertTrue(snap.get("ok"))
                ex = snap.get("learning_profile_excerpt") or {}
                self.assertEqual(ex.get("interests"), ["тест"])
                self.assertIn("behavior_session", snap)
                self.assertIn("user_digital_profile", snap)
                self.assertIn("psychology_profile_excerpt", snap)


if __name__ == "__main__":
    unittest.main()
