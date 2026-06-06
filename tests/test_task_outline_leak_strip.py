from core.brain.response_finalize import finalize_user_reply, looks_like_prompt_instruction_leak


def test_task_outline_line_is_leak():
    assert looks_like_prompt_instruction_leak("- task_outline:\n")
    assert finalize_user_reply("- task_outline:\n") == ""


def test_photo_count_line_is_leak():
    assert looks_like_prompt_instruction_leak("- photo_count: 1\n")
    assert finalize_user_reply("- photo_count: 1\n") == ""
