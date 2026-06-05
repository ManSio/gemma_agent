"""schedule_storage: миграция schedules.json → user_schedules.json."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import schedule_storage as ss


class ScheduleStorageMigrationTests(unittest.TestCase):
    def test_migrate_legacy_into_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sched_dir = root / "data" / "schedule"
            sched_dir.mkdir(parents=True)
            legacy = sched_dir / "schedules.json"
            legacy.write_text(
                json.dumps(
                    {
                        "u1": {
                            "user_id": "u1",
                            "schedule": {"reminders": [{"event": "math"}]},
                            "created_at": "2026-01-01",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(ss, "_root", return_value=root):
                n = ss.migrate_legacy_plugin_file()
                self.assertEqual(1, n)
                canon = ss.canonical_path()
                self.assertTrue(canon.is_file())
                data = json.loads(canon.read_text(encoding="utf-8"))
                self.assertIn("u1", data)
                self.assertIn("reminders", data["u1"])
                self.assertFalse(legacy.is_file())
                self.assertTrue(ss._marker_path().is_file())


if __name__ == "__main__":
    unittest.main()
