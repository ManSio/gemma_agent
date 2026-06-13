import pytest

from core.brain import _brain_hot_path_slim_eligible


def _ctx(**kwargs):
    base = {
        "group_id": None,
        "file_context": {},
        "operator_rules_brain_addon": "",
        "ephemeral_lessons_brain_addon": "",
        "ocr_text": "",
    }
    base.update(kwargs)
    return base


def test_hot_slim_basic_dm():
    assert _brain_hot_path_slim_eligible(
        user_text="Привет, как дела?",
        context=_ctx(),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_hot_slim_rejects_url():
    assert not _brain_hot_path_slim_eligible(
        user_text="смотри https://example.com",
        context=_ctx(),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_hot_slim_rejects_group_by_default():
    assert not _brain_hot_path_slim_eligible(
        user_text="ок",
        context=_ctx(group_id="-100"),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_hot_slim_group_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BRAIN_HOT_PATH_SLIM_IN_GROUPS", "true")
    assert _brain_hot_path_slim_eligible(
        user_text="ок",
        context=_ctx(group_id="-100"),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="short",
        group_chat_addon_len=50,
    )


def test_hot_slim_rejects_missing_facts():
    assert not _brain_hot_path_slim_eligible(
        user_text="привет",
        context=_ctx(),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=["timezone"],
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_hot_slim_rejects_nested_tier():
    assert not _brain_hot_path_slim_eligible(
        user_text="Как дела? И ещё: что по погоде?",
        context=_ctx(dialogue_state={"task_tier": "nested"}),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
        task_tier="nested",
    )


def test_hot_slim_rejects_urls_chron():
    assert not _brain_hot_path_slim_eligible(
        user_text="продолжи",
        context=_ctx(),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
        urls_chron=["https://example.com/article"],
    )


def test_hot_slim_rejects_telegram_reply_context():
    assert not _brain_hot_path_slim_eligible(
        user_text="продолжи",
        context=_ctx(telegram_reply_context="Цитата: обсуждаем договор аренды до 2027 года."),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_hot_slim_rejects_document_intake():
    assert not _brain_hot_path_slim_eligible(
        user_text="что в документе?",
        context=_ctx(document_intake={"filename": "lease.pdf", "text": "договор аренды"}),
        use_slim_image=False,
        skill_name=None,
        skill_output={},
        image_intent=None,
        missing_facts=[],
        group_transcript_compact="",
        group_chat_addon_len=0,
    )
