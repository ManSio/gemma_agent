"""MEM-4: session_digest не в prompt при полном recent."""

import os
from unittest.mock import patch

from core.brain.pipeline_postprocess import (
    get_session_digest_for_prompt,
    session_digest_skip_when_recent_full,
)
from core.session_digest import record_turn, reset_session_digest_buffers


def test_skip_when_recent_at_limit():
    recent = [{"role": "user", "text": f"m{i}"} for i in range(10)]
    with patch.dict(os.environ, {"BRAIN_STANDARD_RECENT_COUNT": "10", "SESSION_DIGEST_SKIP_WHEN_RECENT_FULL": "true"}, clear=False):
        assert session_digest_skip_when_recent_full(recent) is True


def test_no_skip_when_recent_below_limit():
    recent = [{"role": "user", "text": "one"}]
    with patch.dict(os.environ, {"BRAIN_STANDARD_RECENT_COUNT": "10", "SESSION_DIGEST_SKIP_WHEN_RECENT_FULL": "true"}, clear=False):
        assert session_digest_skip_when_recent_full(recent) is False


def test_get_session_digest_for_prompt_empty_when_skipped():
    reset_session_digest_buffers()
    with patch.dict(
        os.environ,
        {
            "BRAIN_STANDARD_RECENT_COUNT": "4",
            "SESSION_DIGEST_SKIP_WHEN_RECENT_FULL": "true",
            "SESSION_DIGEST_ENABLED": "true",
        },
        clear=False,
    ):
        record_turn(
            user_id="u1",
            user_text="тестовый вопрос про небо",
            outcome="ok",
            intent="chitchat",
            module="brain",
        )
        recent = [{"role": "user", "text": f"q{i}"} for i in range(4)]
        assert get_session_digest_for_prompt("u1", None, recent_dialogue=recent) == ""
