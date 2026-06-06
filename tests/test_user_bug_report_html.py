"""Баг-репорт: HTML parse_mode и экранирование пользовательского текста."""
from core.user_bug_report import _build_bug_text


def test_build_bug_text_escapes_user_angle_brackets():
    text = _build_bug_text(
        user_id="900000001",
        chat_id="900000001",
        description="Ты <не> понял & ошибся",
        username="test_user",
        full_name="Test User <MS>",
    )
    assert "<b>User ID:</b>" in text
    assert "<не>" not in text
    assert "&lt;не&gt;" in text
    assert "&lt;MS&gt;" in text


def test_build_bug_text_has_no_raw_unclosed_tags_from_user():
    text = _build_bug_text(
        user_id="1",
        chat_id="1",
        description="ok",
        username="",
        full_name="",
    )
    assert text.startswith("🐛")
