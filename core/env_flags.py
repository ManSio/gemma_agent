"""Флаги окружения без зависимостей от других модулей core (импорт безопасен из logging_setup)."""
from __future__ import annotations

import os


def env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def gemma_core_log_full() -> bool:
    """
    Режим «рассказать в логах всё про ядро»: см. GEMMA_CORE_LOG_FULL в .env.example
    и docker-compose (удобно для docker logs | jq).
    """
    return env_truthy("GEMMA_CORE_LOG_FULL")
