"""
Дополнительный текст для контекста мозга (самокоррекция, «системные директивы»).

Файл по умолчанию: data/runtime/system_directive_addon.txt
Переопределение: SYSTEM_DIRECTIVE_ADDON_PATH=/abs/path.txt

Содержимое объединяется с brain_context_addon из operator_rules.json в поле
operator_rules_brain_addon (см. orchestrator._assemble_brain_context).
Пример текста: config/system_directive_addon_v3.example.txt (v2: …_v2.example.txt)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path(__file__).resolve().parent.parent


def system_directive_addon_path() -> Path:
    raw = (os.getenv("SYSTEM_DIRECTIVE_ADDON_PATH") or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (_repo_root() / p).resolve()
    base = Path((os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip())
    if not base.is_absolute():
        base = _repo_root() / base
    return (base / "system_directive_addon.txt").resolve()


def load_system_directive_brain_addon() -> str:
    p = system_directive_addon_path()
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("system_directive_addon: cannot read %s: %s", p, e)
        return ""
