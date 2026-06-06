"""Генерация комментариев к переменным .env (эвристики + ручные подсказки)."""
from __future__ import annotations

import re
from typing import List

# Ручные развёрнутые описания (приоритет над эвристикой)
MANUAL: dict[str, List[str]] = {
    "TELEGRAM_TOKEN": [
        "# TELEGRAM_TOKEN — токен бота от @BotFather.",
        "#   Пусто — бот не стартует. Утечка → /revoke в BotFather и новый токен.",
        "#   Пример: TELEGRAM_TOKEN=123456:ABC-DEF...",
    ],
    "OPENROUTER_API_KEY": [
        "# OPENROUTER_API_KEY — ключ OpenRouter для LLM/vision/STT.",
        "#   Пусто — мозг и платные вызовы недоступны (остаются эвристики/RSS).",
    ],
    "BRAIN_NEWS_DIRECT_FROM_SEARCH": [
        "# BRAIN_NEWS_DIRECT_FROM_SEARCH — формат ответа на «новости».",
        "#   false — LLM: 3–4 предложения на пункт (что внутри новости).",
        "#   true — только заголовки из RSS без LLM (быстро, но сухо).",
    ],
    "TELEGRAM_PIPELINE_PRIVATE_PARALLEL": [
        "# TELEGRAM_PIPELINE_PRIVATE_PARALLEL — параллельные ходы в личке.",
        "#   1 — последовательно (рекомендуется): нет ответа «на прошлый вопрос».",
        "#   2+ — параллель; риск перепутанного контекста (см. INCIDENT_2026-05-20).",
    ],
    "GEMMA_AUTOPILOT_MODE": [
        "# GEMMA_AUTOPILOT_MODE — пакет дефолтов автопилота (on/off).",
        "#   on — подставляет пустые ключи из autopilot_mode.py (логи, tool chain…).",
        "#   off — только то, что явно задано в .env.",
    ],
    "SEARXNG_INSTANCE_URL": [
        "# SEARXNG_INSTANCE_URL — свой SearXNG для UniversalSearch.",
        "#   Пусто — fallback DuckDuckGo/Tavily/Brave по другим флагам.",
        "#   Пример: http://searxng.local:8080",
    ],
    "MEM0_LOCAL": [
        "# MEM0_LOCAL — память через локальный mem0_server (true) или облако Mem0.",
        "#   true + MEM0_API_URL=http://127.0.0.1:8001 — типично на deploy-host.",
    ],
}

_BOOL_SUFFIXES = ("_ENABLED", "_REQUIRED", "_ALLOWED", "_NOTIFY", "_SLIM", "_STICKY")
_SECRET_KEYS = frozenset(
    {
        "TELEGRAM_TOKEN",
        "OPENROUTER_API_KEY",
        "OPENROUTER_API_KEY_DEV",
        "API_TOKEN",
        "QDRANT_API_KEY",
        "MEM0_API_KEY",
        "ENCRYPTION_KEY",
        "SECURITY_AES_KEY",
        "SECURITY_SALT",
        "BRAVE_SEARCH_API_KEY",
        "TAVILY_API_KEY",
        "LINK_REPUTATION_API_KEY",
    }
)


def _is_secret_key(key: str) -> bool:
    if key in _SECRET_KEYS:
        return True
    u = key.upper()
    return any(
        x in u
        for x in (
            "TOKEN",
            "API_KEY",
            "SECRET",
            "PASSWORD",
            "PRIVATE_KEY",
            "_KEY_DEV",
        )
    )


def _is_boolish(key: str, value: str) -> bool:
    v = (value or "").strip().lower()
    if v in ("true", "false", "1", "0", "on", "off", "yes", "no"):
        return True
    if any(key.endswith(s) for s in _BOOL_SUFFIXES):
        return True
    if key.startswith(("BRAIN_", "TELEGRAM_", "API_", "MCE_", "GOAL_")) and "ENABLED" in key:
        return True
    return False


def _is_numeric_limit(key: str) -> bool:
    return bool(
        re.search(
            r"(_MAX_|_MIN_|_LIMIT|_COUNT|_ITEMS|_CHARS|_BYTES|_SEC|_MS|_RPM|_THRESHOLD|_TTL|_INTERVAL|_TIMEOUT|_PORT|_HOUR)",
            key,
        )
    )


def _humanize_key(key: str) -> str:
    return key.lower().replace("_", " ")


def generate_doc_block(key: str, value: str, existing: List[str]) -> List[str]:
    """Комментарии перед KEY=; не дублирует уже развёрнутый блок."""
    if existing:
        blob = "\n".join(existing).lower()
        if len(existing) >= 2 and (
            "пример" in blob
            or ("true" in blob and "false" in blob)
            or "вкл" in blob
            or "выкл" in blob
            or len(existing) >= 4
        ):
            return list(existing)

    if key in MANUAL:
        return list(MANUAL[key])

    lines: List[str] = [f"# {key} — {_humanize_key(key)}."]
    if _is_secret_key(key):
        lines.append("#   Секрет — не коммитить; хранить только в .env на сервере/ПК.")
        lines.append(f"#   Пример: {key}=<значение из панели провайдера>")
        return lines

    if _is_boolish(key, value):
        lines.extend(
            [
                "#   true / 1 / on / yes — включено.",
                "#   false / 0 / off / no — выключено.",
                f"#   Пример: {key}=true",
            ]
        )
        return lines

    if _is_numeric_limit(key):
        lines.extend(
            [
                "#   Число; порог/лимит из кода читает эту переменную (без «магии» в .py).",
                f"#   Пример: {key}=10",
            ]
        )
        return lines

    if key.endswith("_PATH") or key.endswith("_DIR") or "PATH" in key:
        lines.extend(
            [
                "#   Путь к файлу или каталогу на диске сервера.",
                f"#   Пример: {key}=data/runtime/example.json",
            ]
        )
        return lines

    if key.endswith("_MODEL") or "MODEL" in key:
        lines.extend(
            [
                "#   Slug модели OpenRouter или локального сервиса.",
                f"#   Пример: {key}=deepseek/deepseek-v4-flash",
            ]
        )
        return lines

    if key.endswith("_URL") or key.endswith("_ENDPOINT"):
        lines.extend(
            [
                "#   URL сервиса; пусто — функция отключена или встроенный fallback.",
                f"#   Пример: {key}=http://127.0.0.1:8001",
            ]
        )
        return lines

    lines.extend(
        [
            "#   Строковое значение; пусто — дефолт в коде.",
            f"#   Пример: {key}=<значение>",
        ]
    )
    return lines
