import json
import os
import tempfile
import unittest
from pathlib import Path

from core.reminder_dispatch import _prune_invalid_user_keys, add_reminder, load_reminders, save_reminders
from tests.fixtures.telegram_test_ids import TEST_USER_UID


class ReminderBogusUidPruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["GEMMA_PROJECT_ROOT"] = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_prune_removes_non_numeric_uids(self) -> None:
        path = Path(self._tmpdir.name) / "data" / "runtime" / "light_reminders.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "users": {
                        "u_by_num": [{"id": "1", "text": "x", "due_ts": 1}],
                        TEST_USER_UID: [{"id": "2", "text": "y", "due_ts": 2}],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        data = load_reminders()
        self.assertTrue(_prune_invalid_user_keys(data))
        save_reminders(data)
        after = load_reminders()
        self.assertNotIn("u_by_num", after.get("users") or {})
        self.assertIn(TEST_USER_UID, after.get("users") or {})

    def test_add_reminder_numeric_uid_kept(self) -> None:
        add_reminder(TEST_USER_UID, "ping", due_ts=1_700_000_000 + 123)
        data = load_reminders()
        self.assertFalse(_prune_invalid_user_keys(data))
        self.assertIn(TEST_USER_UID, data.get("users") or {})
