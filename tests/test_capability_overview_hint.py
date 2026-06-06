"""user_requests_capability_overview и лимит второго прохода мозга."""

from core.brain.text_helpers import brain_second_stage_max_tokens, user_requests_capability_overview


def test_user_requests_capability_tools_listing():
    assert user_requests_capability_overview("покажи свои инструменты и диагностику")


def test_user_requests_capability_typo_kogde_for_code():
    assert user_requests_capability_overview("что нового, может что заметил в когде?")


def test_user_requests_capability_negative():
    assert not user_requests_capability_overview("просто привет")


def test_brain_second_stage_max_tokens_default(monkeypatch):
    monkeypatch.delenv("BRAIN_SECOND_MAX_TOKENS", raising=False)
    assert brain_second_stage_max_tokens() == 1200


def test_brain_second_stage_max_tokens_env(monkeypatch):
    monkeypatch.setenv("BRAIN_SECOND_MAX_TOKENS", "2000")
    assert brain_second_stage_max_tokens() == 2000
