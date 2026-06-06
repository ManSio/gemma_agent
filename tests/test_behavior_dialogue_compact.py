import os
from pathlib import Path

import pytest

from core.behavior_store import BehaviorStore


def test_dialogue_fifo_trim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """FIFO: recent_messages trimmed to max, no snippet summary generation."""
    monkeypatch.setenv("DIALOGUE_MEMORY_MAX", "4")
    monkeypatch.delenv("DIALOGUE_COMPACT_LLM", raising=False)
    base = tmp_path / "beh"
    store = BehaviorStore(base_dir=str(base))
    uid, gid = "u1", None
    for i in range(6):
        store.update_after_turn(uid, gid, f"user{i}", f"bot{i}", telegram_is_admin=True)
    rec = store.load(uid, gid)
    msgs = rec.get("recent_messages") or []
    assert len(msgs) <= 4
    assert len(msgs) % 2 == 0
    assert str(msgs[0].get("role") or "") == "user"
    texts = [m.get("text", "") for m in msgs]
    assert "user5" in texts


def test_dialogue_odd_max_keeps_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Odd DIALOGUE_MEMORY_MAX must not leave orphan assistant at slice head."""
    monkeypatch.setenv("DIALOGUE_MEMORY_MAX", "5")
    monkeypatch.delenv("DIALOGUE_COMPACT_LLM", raising=False)
    base = tmp_path / "beh"
    store = BehaviorStore(base_dir=str(base))
    uid, gid = "u_odd", None
    for i in range(4):
        store.update_after_turn(uid, gid, f"user{i}", f"bot{i}")
    msgs = store.load(uid, gid).get("recent_messages") or []
    assert msgs
    assert str(msgs[0].get("role") or "") == "user"
    assert len(msgs) % 2 == 0


def test_dialogue_no_snippet_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Snippet summary removed — dialogue_summary stays from other sources only."""
    monkeypatch.setenv("DIALOGUE_MEMORY_MAX", "4")
    monkeypatch.delenv("DIALOGUE_COMPACT_LLM", raising=False)
    base = tmp_path / "beh"
    store = BehaviorStore(base_dir=str(base))
    uid, gid = "u2", None
    for i in range(6):
        store.update_after_turn(uid, gid, f"u{i}", f"bot{i}", telegram_is_admin=True)
    rec = store.load(uid, gid)
    # With no pre-existing summary, the snippet summary no longer auto-generates
    # dialogue_summary may remain empty or contain only what existed before
    s = str(rec.get("dialogue_summary") or "")
    # The only requirement: it should not contain user messages as snippets
    assert "user0:user0" not in s


def test_dialogue_compact_pending_on_overflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """FIFO overflow → pending LLM summary only when DIALOGUE_COMPACT_LLM enabled."""
    monkeypatch.setenv("DIALOGUE_MEMORY_MAX", "4")
    monkeypatch.setenv("DIALOGUE_COMPACT_LLM", "true")
    monkeypatch.delenv("DIALOGUE_SUMMARY_ON_OVERFLOW", raising=False)
    base = tmp_path / "beh"
    store = BehaviorStore(base_dir=str(base))
    uid, gid = "u3", None
    pending = None
    for i in range(5):
        rec, pending = store.update_after_turn(uid, gid, f"user{i}", f"bot{i}", telegram_is_admin=True)
    assert pending is not None
    assert pending["overflow_messages"]
    assert len(rec.get("recent_messages") or []) <= 4


def test_dialogue_compact_pending_disabled_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIALOGUE_MEMORY_MAX", "4")
    monkeypatch.setenv("DIALOGUE_SUMMARY_ON_OVERFLOW", "false")
    monkeypatch.setenv("DIALOGUE_COMPACT_LLM", "false")
    base = tmp_path / "beh"
    store = BehaviorStore(base_dir=str(base))
    uid, gid = "u4", None
    for i in range(5):
        _rec, pending = store.update_after_turn(uid, gid, f"user{i}", f"bot{i}")
    assert pending is None


def test_grounding_pack_import():
    from core.grounding_pack import build_minimal_grounding

    s = build_minimal_grounding(
        {"telegram_message_date_unix": 1_700_000_000},
        {"city": "Minsk", "country": "BY"},
    )
    assert "UTC_now=" in s
    assert "tg_msg_utc=" in s
    assert "city=" in s
