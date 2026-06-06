"""
Текст учебника по плагинам с диска — подмешивается в промпт мозга при SelfProgramming.*.

Кэш на процесс; отключение: BRAIN_PLUGIN_AUTHOR_DOCS=false.
Обрезка: BRAIN_PLUGIN_AUTHOR_DOCS_MAX_CHARS (0 = без лимита).
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from core.brain.env import env_flag

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("plugin_author_context: не прочитан %s: %s", path, e)
        return ""


@lru_cache(maxsize=1)
def _handbook_cached() -> str:
    p = _repo_root() / "docs" / "PLUGIN_AUTHOR_HANDBOOK_RU.md"
    if not p.is_file():
        logger.warning("plugin_author_context: нет файла %s", p)
        return ""
    return _read_utf8(p)


def invalidate_plugin_author_handbook_cache() -> None:
    """Для тестов или после деплоя новой версии docs без рестарта (редко)."""
    _handbook_cached.cache_clear()


def plugin_author_handbook_for_prompt() -> str:
    """
    Полный текст учебника (с заголовком-секцией для LLM) или пустая строка.
    """
    if not env_flag("BRAIN_PLUGIN_AUTHOR_DOCS", default=True):
        return ""
    body = _handbook_cached().strip()
    if not body:
        return ""
    max_raw = (os.getenv("BRAIN_PLUGIN_AUTHOR_DOCS_MAX_CHARS") or "").strip()
    try:
        max_c = int(max_raw) if max_raw else 0
    except ValueError:
        max_c = 0
    if max_c > 0 and len(body) > max_c:
        body = body[: max(0, max_c - 30)] + "\n\n… [обрезано: BRAIN_PLUGIN_AUTHOR_DOCS_MAX_CHARS]"

    return (
        "\n\n---\n"
        "Ниже — полный учебник автора плагинов проекта (файл `docs/PLUGIN_AUTHOR_HANDBOOK_RU.md`). "
        "При вызове `SelfProgramming.generate_module`, обсуждении `module.json` / `execute` и правках "
        "в `modules/` опирайся на него как на обязательную инструкцию.\n\n"
        + body
    )
