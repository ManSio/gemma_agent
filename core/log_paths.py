"""
Единый путь к файлу лога процесса (stdout дублируется сюда из setup_logging).
"""
from __future__ import annotations

import os
from pathlib import Path


def _truthy_off(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def resolved_process_log_file_path() -> str:
    """
    Путь к лог-файлу на диске. Пустая строка, если явно отключено (GEMMA_LOG_FILE_OFF).
    Иначе GEMMA_LOG_FILE / LOG_FILE или по умолчанию BEHAVIOR_DATA_DIR/logs/gemma_bot.log.
    """
    if _truthy_off("GEMMA_LOG_FILE_OFF"):
        return ""
    p = (os.getenv("GEMMA_LOG_FILE") or os.getenv("LOG_FILE") or "").strip()
    if p:
        return p
    base = (os.getenv("BEHAVIOR_DATA_DIR") or "").strip() or os.path.join(os.getcwd(), "data")
    return str(Path(base) / "logs" / "gemma_bot.log")
