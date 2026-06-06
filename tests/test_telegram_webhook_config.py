"""WEBHOOK_URL placeholder → polling."""

from core.telegram_webhook_config import (
    is_webhook_url_placeholder,
    resolve_telegram_webhook_url,
)


def test_placeholder_hosts():
    assert is_webhook_url_placeholder("https://your.domain.com")
    assert is_webhook_url_placeholder("http://your.domain.com/webhook")
    assert not is_webhook_url_placeholder("https://bot.example.org")


def test_resolve_empty_on_placeholder():
    assert resolve_telegram_webhook_url("https://your.domain.com") == ""
    assert resolve_telegram_webhook_url("") == ""
    assert resolve_telegram_webhook_url("https://gemma.vpn.example.com/hook") != ""


def test_resolve_from_env():
    env = {"WEBHOOK_URL": "https://your.domain.com"}
    assert resolve_telegram_webhook_url(env=env) == ""
    env2 = {"WEBHOOK_URL": "https://ai.mydomain.net/telegram"}
    assert resolve_telegram_webhook_url(env=env2) == "https://ai.mydomain.net/telegram"
