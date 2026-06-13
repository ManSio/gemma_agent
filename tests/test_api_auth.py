import pytest

from core.api_auth import (
    DEFAULT_API_TOKEN,
    enforce_startup_api_token_config,
    is_api_enabled_from_env,
    is_default_api_token,
    is_production_app_env,
    normalize_api_token,
)


def test_normalize_api_token_strips_quotes():
    assert normalize_api_token('  "secret"  ') == "secret"


def test_is_default_api_token_placeholder():
    assert is_default_api_token(DEFAULT_API_TOKEN) is True
    assert is_default_api_token("real-secret") is False


def test_is_production_app_env_values(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert is_production_app_env() is True
    monkeypatch.setenv("APP_ENV", "prod")
    assert is_production_app_env() is True
    monkeypatch.setenv("APP_ENV", "development")
    assert is_production_app_env() is False


def test_is_api_enabled_from_env(monkeypatch):
    monkeypatch.setenv("API_ENABLED", "true")
    assert is_api_enabled_from_env() is True
    monkeypatch.setenv("API_ENABLED", "false")
    assert is_api_enabled_from_env() is False


def test_enforce_allows_default_when_api_disabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_ENABLED", "false")
    enforce_startup_api_token_config(DEFAULT_API_TOKEN)


def test_enforce_blocks_default_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_ENABLED", "false")
    with pytest.raises(SystemExit):
        enforce_startup_api_token_config(DEFAULT_API_TOKEN)


def test_enforce_blocks_default_when_api_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_ENABLED", "true")
    with pytest.raises(SystemExit):
        enforce_startup_api_token_config(DEFAULT_API_TOKEN)


def test_enforce_allows_custom_token_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_ENABLED", "true")
    enforce_startup_api_token_config("configured-secret-token")
