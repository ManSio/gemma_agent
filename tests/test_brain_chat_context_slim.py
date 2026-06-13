import pytest

from core.brain.hot_path import brain_chat_context_slim_eligible


def _ctx(**kwargs):
    base = {
        "group_id": None,
        "file_context": {},
        "predictive_hint": {},
        "ocr_text": "",
    }
    base.update(kwargs)
    return base


def test_chat_context_slim_long_plain_text():
    long_text = "рецензия: " + ("слово " * 800)
    assert brain_chat_context_slim_eligible(
        user_text=long_text,
        context=_ctx(),
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_rejects_urls_in_thread():
    assert not brain_chat_context_slim_eligible(
        user_text="продолжи",
        context=_ctx(),
        task_tier="shallow",
        urls_chron=["https://a.example/x"],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_rejects_deep_tier():
    assert not brain_chat_context_slim_eligible(
        user_text="разбор сценария",
        context=_ctx(),
        task_tier="deep",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_rejects_skill_priority():
    assert not brain_chat_context_slim_eligible(
        user_text="сделай картинку",
        context=_ctx(predictive_hint={"skill_priority": ["image"]}),
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BRAIN_CHAT_CONTEXT_SLIM", "false")
    assert not brain_chat_context_slim_eligible(
        user_text="x" * 200,
        context=_ctx(),
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_rejects_telegram_reply_context():
    assert not brain_chat_context_slim_eligible(
        user_text="дальше",
        context=_ctx(telegram_reply_context="Ответ на: обсуждение контракта поставки."),
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_rejects_heavy_operator_rules():
    assert not brain_chat_context_slim_eligible(
        user_text="ок",
        context=_ctx(operator_rules_brain_addon="rule " * 500),
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )


def test_chat_context_slim_rejects_document_intake():
    assert not brain_chat_context_slim_eligible(
        user_text="резюмируй",
        context=_ctx(document_intake={"filename": "scan.pdf", "text": "invoice totals"}),
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
        group_transcript_compact="",
        group_chat_addon_len=0,
    )
