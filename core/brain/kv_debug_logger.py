"""
KV Debug Logger — трассировка всей цепочки кеша: сессия → сборка промпта → OpenRouter → ответ.

Пишет в data/runtime/kv_debug.jsonl одну строку на каждый brain-вызов с полями:
- session_id, epoch, bucket, reset_reason
- prompt_breakdown (chars/tokens каждого блока)
- system_prompt_hash (первых 12 hex) — для отслеживания изменений статической головы
- openrouter: cached_tok, cache_write_tok, latency_ms
- turn_count, kv_reuse_allowed

Включение: GEMMA_KV_DEBUG_LOG=true (по умолчанию true)
Лог-файл: GEMMA_KV_DEBUG_LOG_PATH (дефолт data/runtime/kv_debug.jsonl)
Лимит строк: GEMMA_KV_DEBUG_LOG_MAX_LINES (дефолт 5000)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_lines_written: int = 0


def _enabled() -> bool:
    raw = os.getenv("GEMMA_KV_DEBUG_LOG")
    if raw is None:
        return True  # включён по умолчанию
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _log_path() -> str:
    raw = (os.getenv("GEMMA_KV_DEBUG_LOG_PATH") or "").strip()
    if raw:
        return raw
    base = os.getenv("GEMMA_KV_DEBUG_DIR") or os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    return os.path.join(base, "kv_debug.jsonl")


def _max_lines() -> int:
    try:
        return max(100, int(os.getenv("GEMMA_KV_DEBUG_LOG_MAX_LINES", "5000")))
    except (ValueError, TypeError):
        return 5000


def _prompt_dump_enabled() -> bool:
    """GEMMA_KV_DEBUG_PROMPT_DUMP=true — включает полный дамп промпта в trace."""
    raw = os.getenv("GEMMA_KV_DEBUG_PROMPT_DUMP")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_for_log(text: str, max_chars: int = 12000) -> str:
    """Обрезать текст до max_chars, показать голову и хвост если превышен."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n... [TRUNCATED {len(text) - max_chars} chars] ...\n" + text[-half:]


def record_kv_trace(entry: Dict[str, Any]) -> None:
    """Записать одну строку трассировки KV-кеша."""
    if not _enabled():
        return
    global _lines_written
    with _lock:
        if _lines_written >= _max_lines():
            return
        path = _log_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            row = {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                **entry,
            }
            line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            _lines_written += 1
        except OSError as e:
            logger.warning("kv_debug_logger write failed: %s", e)


def kv_debug_log_reset() -> int:
    """Очистить лог-файл, сбросить счётчик. Возвращает число удалённых строк (приблизительно)."""
    global _lines_written
    path = _log_path()
    count = 0
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                count = sum(1 for _ in f)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
    except OSError as e:
        logger.warning("kv_debug_log_reset failed: %s", e)
    _lines_written = 0
    return count


def kv_debug_log_path() -> str:
    return _log_path()


def kv_debug_log_stats() -> Dict[str, Any]:
    with _lock:
        return {
            "enabled": _enabled(),
            "path": _log_path(),
            "lines_written": _lines_written,
            "max_lines": _max_lines(),
        }
