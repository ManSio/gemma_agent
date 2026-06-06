import time
from pathlib import Path

from core.reminder_dispatch import _compute_next_recurring_ts, add_recurring_reminder, load_reminders


def test_recurring_stored(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    rid = add_recurring_reminder("u_r", "йога", dows={2}, hour=7, minute=0)
    data = load_reminders()
    items = data["users"]["u_r"]
    assert any(it.get("id") == rid and it.get("recurring") for it in items)


def test_compute_next_recurring_future(monkeypatch):
    monkeypatch.setenv("REMINDER_DEFAULT_TIMEZONE", "Europe/Moscow")
    now = int(time.time())
    nxt = _compute_next_recurring_ts(dows={0, 1, 2, 3, 4}, hour=9, minute=0, user_id="u", after_ts=now)
    assert nxt > now
