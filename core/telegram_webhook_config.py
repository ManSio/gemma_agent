"""Разбор WEBHOOK_URL: placeholder из .env.example не должен ронять polling."""

from __future__ import annotations

import os
from urllib.parse import urlparse

# Синхронно с scripts/sync_env_from_example.py и docs/UPGRADE_PLAN_2026_Q2_RU.md
_WEBHOOK_PLACEHOLDER_HOSTS = frozenset(
    {
        "your.domain.com",
        "your-domain.com",
        "example.com",
        "localhost",
    }
)


def is_webhook_url_placeholder(url: str) -> bool:
    """True если URL из шаблона .env.example, а не реальный прод-домен."""
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except Exception:
        return True
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return True
    if host in _WEBHOOK_PLACEHOLDER_HOSTS:
        return True
    if host.startswith("your.") or host.endswith(".example") or host.endswith(".local"):
        return True
    return False


def resolve_telegram_webhook_url(
    raw: str | None = None,
    *,
    env: os._Environ[str] | None = None,
) -> str:
    """
    Эффективный WEBHOOK_URL для main.py.
    Пустая строка → polling (aiogram getUpdates).
  """
    source = env if env is not None else os.environ
    value = (raw if raw is not None else source.get("WEBHOOK_URL", "")).strip()
    if not value or is_webhook_url_placeholder(value):
        return ""
    return value
