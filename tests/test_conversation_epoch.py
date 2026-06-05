"""conversation_epoch в behavior_store."""

from datetime import datetime, timezone

from core.conversation_epoch import bump_conversation_epoch, get_epoch_id, maybe_idle_bump_epoch


def test_bump_increments_id():
    rec = {"conversation_epoch": {"id": 2, "started_at": "", "last_activity_at": ""}}
    new_id = bump_conversation_epoch(rec, user_id="u1", group_id=None, reason="test")
    assert new_id == 3
    assert get_epoch_id(rec) == 3


def test_idle_no_bump_when_recent():
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rec = {
        "conversation_epoch": {
            "id": 1,
            "started_at": now,
            "last_activity_at": now,
        }
    }
    assert maybe_idle_bump_epoch(rec, user_id="u1", group_id=None) is None
    assert get_epoch_id(rec) == 1


def test_bump_clear_dialogue_removes_dialogue_slot():
    rec = {
        "conversation_epoch": {"id": 2, "started_at": "", "last_activity_at": ""},
        "routing_prefs": {
            "dialogue_slot": {"kind": "article_thread", "turns_left": 5, "meta": {}, "set_at": ""},
        },
    }
    bump_conversation_epoch(
        rec,
        user_id="u1",
        group_id=None,
        reason="test",
        clear_dialogue=True,
    )
    assert "dialogue_slot" not in (rec.get("routing_prefs") or {})
