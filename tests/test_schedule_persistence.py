import json
from pathlib import Path

from core.schedule_module import ScheduleModule
from core.schedule_storage import canonical_path


def test_schedule_add_event_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    m1 = ScheduleModule()
    assert m1.add_event("u42", {"title": "урок", "text": "напомни завтра в 10:00 урок"})
    path = canonical_path()
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "u42" in raw
    m2 = ScheduleModule()
    assert m2.get_schedule("u42") is not None
    assert len(m2.get_schedule("u42").get("events") or []) == 1
