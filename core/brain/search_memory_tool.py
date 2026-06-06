"""
search_memory — архивная память (MemGPT-style) для deep профиля.

Инструмент ищет релевантные фрагменты из полной истории диалога
с пользователем и возвращает короткие контекстные сниппеты.

Версия: 1.0.0
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SEARCH_MEMORY_VERSION = "1.0.0"

_DB_PATH: Optional[str] = None
_DB_LOCK = threading.Lock()


def _resolve_db_path() -> str:
    global _DB_PATH
    if _DB_PATH:
        return _DB_PATH
    base = os.getenv("MESSAGE_ARCHIVE_DB_DIR", os.path.join("data", "message_archive"))
    _DB_PATH = os.path.join(base, "dialogue.db")
    return _DB_PATH


def _ensure_table() -> None:
    """Создаёт таблицу сообщений, если её ещё нет."""
    db_path = _resolve_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _DB_LOCK:
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts REAL NOT NULL,
                    group_id TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_user_ts
                ON messages(user_id, ts DESC)
            """)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                    USING fts5(user_id, role, text, content=messages, content_rowid=id)
                """)
            except Exception:
                # FTS5 может отсутствовать — fallback на LIKE
                logger.debug("FTS5 not available for search_memory, using LIKE fallback")
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("search_memory _ensure_table: %s", exc)


def _fts_available() -> bool:
    """Проверяет, доступен ли FTS5."""
    db_path = _resolve_db_path()
    with _DB_LOCK:
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
            )
            available = cur.fetchone() is not None
            conn.close()
            return available
        except Exception:
            return False


def _fts_search(query: str, user_id: str, limit: int = 5) -> List[str]:
    """Полнотекстовый поиск через FTS5."""
    db_path = _resolve_db_path()
    with _DB_LOCK:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT m.text, m.role, m.ts
            FROM messages_fts fts
            JOIN messages m ON fts.rowid = m.id
            WHERE fts.messages_fts MATCH ? AND m.user_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, user_id, limit),
        ).fetchall()
        conn.close()
    results: List[str] = []
    for r in rows:
        text = str(r["text"] or "")[:1200]
        role = str(r["role"] or "?")
        ts_val = r["ts"]
        ts_str = ""
        if ts_val:
            try:
                from datetime import datetime, timezone
                ts_str = datetime.fromtimestamp(float(ts_val), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'search_memory_tool', e, exc_info=True)
        label = f"[{role}]" + (f" ({ts_str})" if ts_str else "")
        results.append(f"{label}: {text}")
    return results


def _like_search(query: str, user_id: str, limit: int = 5) -> List[str]:
    """Поиск через LIKE (fallback при отсутствии FTS5)."""
    db_path = _resolve_db_path()
    terms = query.split()
    clauses = " AND ".join(["text LIKE ?" for _ in terms])
    params = ["%" + t + "%" for t in terms] + [user_id, limit]
    with _DB_LOCK:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        sql = f"""
            SELECT text, role, ts FROM messages
            WHERE {clauses} AND user_id = ?
            ORDER BY ts DESC
            LIMIT ?
        """
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            conn.close()
            return []
        conn.close()
    results: List[str] = []
    for r in rows:
        text = str(r["text"] or "")[:1200]
        role = str(r["role"] or "?")
        ts_val = r["ts"]
        ts_str = ""
        if ts_val:
            try:
                from datetime import datetime, timezone
                ts_str = datetime.fromtimestamp(float(ts_val), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'search_memory_tool', e, exc_info=True)
        label = f"[{role}]" + (f" ({ts_str})" if ts_str else "")
        results.append(f"{label}: {text}")
    return results


def _fallback_search(query: str, user_id: str, limit: int = 5) -> List[str]:
    """Поиск через JSON-архив сообщений (если нет SQLite)."""
    try:
        from core.message_archive import load_message_archive_items
        items = load_message_archive_items(user_id, None)
    except Exception:
        return []
    q_low = query.lower().split()
    scored: List[tuple[int, Dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").lower()
        score = sum(1 for t in q_low if t in text)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    results: List[str] = []
    for _, item in scored[:limit]:
        text = str(item.get("text") or "")[:1200]
        role = str(item.get("role") or "?")
        results.append(f"[{role}]: {text}")
    return results


class SearchMemoryModule:
    """
    Инструмент search_memory для поиска в архиве диалога.
    Подключается только для deep профиля.
    """

    BRAIN_LITE_INCLUDE = False  # не включать в lite/auto, только deep

    async def search_memory(self, query: str, user_id: str = "", limit: int = 5) -> Dict[str, Any]:
        """
        Искать в полной истории диалога с пользователем.
        Возвращает релевантные фрагменты прошлых разговоров.
        Использовать, когда нужно вспомнить факты, упоминавшиеся ранее.

        Args:
            query: Поисковый запрос, несколько слов.
            user_id: ID пользователя (ядро подставит автоматически).
            limit: Максимальное число результатов (по умолчанию 5).
        """
        q = str(query or "").strip()
        uid = str(user_id or "").strip()
        lim = max(1, min(int(limit or 5), 10))
        if not q:
            return {"ok": False, "error": "empty query", "results": []}
        if not uid:
            return {"ok": False, "error": "no user_id", "results": []}

        _ensure_table()

        results: List[str] = []
        if _fts_available():
            try:
                # FTS5 ожидает raw query; на случай спецсимволов чистим
                safe_q = re.sub(r'[^\w\s\-а-яёА-ЯЁ]', ' ', q).strip()
                if safe_q:
                    results = _fts_search(safe_q, uid, lim)
            except Exception as exc:
                logger.debug("search_memory FTS5: %s, falling back to LIKE", exc)
                results = []
        if not results:
            try:
                results = _like_search(q, uid, lim)
            except Exception:
                results = []

        # Если SQLite-путь не дал результатов, пробуем JSON-архив
        if not results:
            try:
                results = _fallback_search(q, uid, lim)
            except Exception:
                results = []

        if not results:
            return {
                "ok": True,
                "found": False,
                "results": [],
                "hint": "Ничего не найдено в архиве по этому запросу.",
            }

        return {
            "ok": True,
            "found": True,
            "results": results,
            "count": len(results),
            "hint": (
                "Если результатов мало — попробуй более общий запрос "
                "или другой синоним. Не выдумывай факты из архива, "
                "если их нет в results."
            ),
        }
