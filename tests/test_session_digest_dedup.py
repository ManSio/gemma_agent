"""session_digest dedup при одной теме."""

from core.session_digest import _should_skip_digest_flush, reset_session_digest_buffers


def test_skip_same_topic_buffer():
    reset_session_digest_buffers()
    buf = [
        {"user_excerpt": "почему небо голубое", "outcome": "ok"},
        {"user_excerpt": "почему небо голубое уточни", "outcome": "ok"},
    ] * 6
    assert _should_skip_digest_flush(buf) is True


def test_no_skip_mixed_topics():
    buf = [
        {"user_excerpt": "почему небо голубое", "outcome": "ok"},
        {"user_excerpt": "как сварить борщ", "outcome": "ok"},
    ] * 6
    assert _should_skip_digest_flush(buf) is False
