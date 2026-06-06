import os
import time
from pathlib import Path
from unittest.mock import patch

from core.reminder_dispatch import _prune_stale_reminders, add_reminder


def test_prune_stale_reminders(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("REMINDER_STALE_DAYS", "1")
    old_due = int(time.time()) - 3 * 86400
    add_reminder("u_stale", "old task", old_due)
    data = {"users": {"u_stale": [{"id": "x", "text": "old", "due_ts": old_due}]}}
    changed = _prune_stale_reminders(data, int(time.time()))
    assert changed is True
    assert data["users"]["u_stale"] == []
