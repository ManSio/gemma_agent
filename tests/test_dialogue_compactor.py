from core.dialogue_compactor import format_overflow_for_prompt


def test_format_overflow_for_prompt():
    s = format_overflow_for_prompt(
        [
            {"role": "user", "text": "hello", "telegram_ts": 100},
            {"role": "assistant", "text": "hi there"},
        ]
    )
    assert "user" in s
    assert "ts=100" in s
    assert "assistant" in s
