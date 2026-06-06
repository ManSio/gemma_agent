import time

from core.brain.response_finalize import finalize_user_reply, looks_like_prompt_instruction_leak
from core.reminder_dispatch import add_reminder, parse_due_ts, persist_reminder_from_schedule_event


def test_finalize_strips_debug_leaks():
    raw = "Привет\nlast_operation=foo\n_text=0.9\nНормальный ответ"
    out = finalize_user_reply(raw)
    assert "last_operation" not in out
    assert "_text=" not in out
    assert "Нормальный ответ" in out


def test_json_fragment_leak_discarded():
    leak = '"..."}], ... (сокращённо до 12000 символов)'
    assert finalize_user_reply(leak) == ""


def test_instruction_leak_discarded():
    leak = (
        "Вызванные инструменты из этого контекста — в ответе опирайся на их результаты.\n"
        "Важно: пользователь также может прикладывать файлы (document_intake). "
        "Если не удалось — кратко сообщи; не пытайся выдать пустые данные как успешные."
    )
    assert looks_like_prompt_instruction_leak(leak)
    assert finalize_user_reply(leak) == ""


def test_tool_instruction_echo_discarded():
    leak = (
        "Инструкция: Дай строго один ответ — текст или один TOOL_CALL.\n"
        "Не придумывай инструменты."
    )
    assert looks_like_prompt_instruction_leak(leak)
    assert finalize_user_reply(leak) == ""


def test_finalize_strips_style_and_json_schema():
    raw = (
        "Style:\n- blended_style_stable: {}\n"
        '"description": "lon",\n'
        "Given the current context and the user's message\n"
        "Алексей, это улица Куйбышева."
    )
    out = finalize_user_reply(raw)
    assert "blended_style" not in out
    assert "description" not in out or "Куйбышева" in out
    assert "Куйбышева" in out


def test_parse_due_relative_minutes():
    due = parse_due_ts("напомни через 15 мин позвонить")
    assert due is not None
    assert due > int(time.time())


def test_persist_reminder_from_schedule_event(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    rid = persist_reminder_from_schedule_event("u1", "в 18:30 купить молоко")
    assert rid
    rid2 = add_reminder("u1", "тест", int(time.time()) + 3600)
    assert rid2
