#!/usr/bin/env python3
"""Единоразовая очистка архивов от format-утечек (_shape=, response_shape= и т.д.).

Проходит по:
- data/users/behavior/*__*.json (на сервере) или data/behavior/*__*.json (локально)
  — чистит recent_messages и dialogue_summary
- data/message_archive/dialogue.db (SQLite) — удаляет строки с токсичными маркерами

Запуск:
    python3 scripts/clean_toxic_history.py
    python3 scripts/clean_toxic_history.py --dry-run  # только показать, не удалять
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sqlite3
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s| %(message)s")
logger = logging.getLogger("clean_toxic_history")

# Токсичные маркеры (lower-case)
TOXIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"_shape="),
    re.compile(r"response_shape="),
    re.compile(r"внешний хинт подсказывает"),
    re.compile(r"внешний хинт"),
    re.compile(r"user_request_type="),
    re.compile(r"часть\s+\d+\s*/\s*\d+"),  # часть 7/7, часть 1/3 и т.п.
    re.compile(r"_shape=short_answer\.?"),
]


def _is_toxic(text: str) -> bool:
    low = text.lower()
    for pat in TOXIC_PATTERNS:
        if pat.search(low):
            return True
    return False


def _clean_messages_list(messages: list) -> tuple[list, int]:
    cleaned: list = []
    removed = 0
    for msg in messages:
        if isinstance(msg, dict):
            text = msg.get("text", "") or msg.get("content", "") or str(msg)
        else:
            text = str(msg)
        if _is_toxic(text):
            removed += 1
            continue
        cleaned.append(msg)
    return cleaned, removed


def _clean_dialogue_summary(summary: str) -> str:
    if not summary:
        return summary
    lines = summary.split("\n")
    clean_lines = [l for l in lines if not _is_toxic(l)]
    return "\n".join(clean_lines)


def _find_behavior_behaviordir(project_root: str) -> str | None:
    """Определить каталог с behavior-файлами (сервер или локально)."""
    candidates = [
        os.path.join(project_root, "data", "users", "behavior"),
        os.path.join(project_root, "data", "behavior"),
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    return None


def _find_archive_db(project_root: str) -> str | None:
    """Определить путь к SQLite-архиву диалога."""
    candidates = [
        os.path.join(project_root, "data", "message_archive", "dialogue.db"),
        os.path.join(project_root, "data", "database.sqlite"),
    ]
    for fp in candidates:
        if os.path.isfile(fp):
            return fp
    return None


def clean_behavior_files(behavior_dir: str, dry_run: bool) -> int:
    """Очистить behavior-файлы (*__*.json). Возвращает число удалённых сообщений."""
    pattern = os.path.join(behavior_dir, "*__*.json")
    files = sorted(glob.glob(pattern))
    total_removed = 0
    total_files_modified = 0

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("  [skip] %s: %s", os.path.basename(fp), e)
            continue

        if not isinstance(data, dict):
            continue

        modified = False

        # 1. Clean recent_messages
        recent = data.get("recent_messages") or []
        if isinstance(recent, list):
            cleaned, removed = _clean_messages_list(recent)
            if removed:
                data["recent_messages"] = cleaned
                total_removed += removed
                modified = True
                logger.info(
                    "  %s %s recent_messages: удалено %d",
                    "[dry-run]" if dry_run else "[clean]",
                    os.path.basename(fp),
                    removed,
                )

        # 2. Clean dialogue_summary
        summary = data.get("dialogue_summary") or ""
        if isinstance(summary, str) and summary:
            clean_summary = _clean_dialogue_summary(summary)
            if clean_summary != summary:
                data["dialogue_summary"] = clean_summary
                modified = True
                logger.info(
                    "  %s %s dialogue_summary: очищено",
                    "[dry-run]" if dry_run else "[clean]",
                    os.path.basename(fp),
                )

        if modified:
            total_files_modified += 1
            if not dry_run:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

    if total_files_modified:
        logger.info(
            "Behavior: изменено %d файлов, удалено %d сообщений",
            total_files_modified,
            total_removed,
        )
    else:
        logger.info("Behavior: чисто, мусор не найден.")
    return total_removed


def clean_archive_db(db_path: str, dry_run: bool) -> int:
    """Очистить SQLite-архив от токсичных строк."""
    if not os.path.isfile(db_path):
        logger.info("Архив SQLite не найден: %s", db_path)
        return 0

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
    except sqlite3.Error as e:
        logger.warning("  [skip] SQLite: %s", e)
        return 0

    # Определить таблицы и столбцы с текстом
    tables_text_cols = []
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [r[0] for r in cur.fetchall()]
    except sqlite3.Error:
        table_names = []

    for tb in table_names:
        try:
            cur.execute(f"PRAGMA table_info(\"{tb}\")")
            cols = [r[1] for r in cur.fetchall()]
            for c in cols:
                if c in ("text", "content", "message", "payload", "body"):
                    tables_text_cols.append((tb, c))
        except sqlite3.Error:
            continue

    if not tables_text_cols:
        # Fallback: message_archive
        tables_text_cols = [("messages", "text"), ("archive", "text")]

    total_removed = 0
    total_rows_modified = 0

    for tb, col in tables_text_cols:
        try:
            # Count
            cur.execute(f"SELECT COUNT(*) FROM \"{tb}\"")
            total_rows = cur.fetchone()[0]
            # Find toxic
            cur.execute(f"SELECT rowid, \"{col}\" FROM \"{tb}\"")
            toxic_ids: list[int] = []
            for row_id, txt in cur.fetchall():
                if txt and _is_toxic(txt):
                    toxic_ids.append(row_id)

            if not toxic_ids:
                continue

            total_removed += len(toxic_ids)
            total_rows_modified += 1
            label = "[dry-run]" if dry_run else "[clean]"
            logger.info(
                "  %s %s.%s: удалено %d / %d строк",
                label,
                tb,
                col,
                len(toxic_ids),
                total_rows,
            )

            if not dry_run:
                placeholders = ",".join("?" for _ in toxic_ids)
                cur.execute(
                    f"DELETE FROM \"{tb}\" WHERE rowid IN ({placeholders})",
                    toxic_ids,
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.warning("  [skip] %s.%s: %s", tb, col, e)
            continue

    conn.close()
    if total_removed:
        logger.info(
            "SQLite-архив: очищено %d таблиц, удалено %d строк",
            total_rows_modified,
            total_removed,
        )
    else:
        logger.info("SQLite-архив: чисто, мусор не найден.")
    return total_removed


def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    behavior_dir = _find_behavior_behaviordir(project_root)
    archive_db = _find_archive_db(project_root)

    if not behavior_dir and not archive_db:
        logger.error("Не найдены ни behavior-файлы, ни архив SQLite.")
        logger.error("Каталог проекта: %s", project_root)
        sys.exit(1)

    mode = "DRY-RUN (только просмотр)" if dry_run else "РЕАЛЬНАЯ ОЧИСТКА"
    logger.info("=== %s ===", mode)
    if behavior_dir:
        logger.info("Behavior: %s", behavior_dir)
    if archive_db:
        logger.info("Архив DB: %s", archive_db)

    total = 0
    if behavior_dir:
        total += clean_behavior_files(behavior_dir, dry_run)
    if archive_db:
        total += clean_archive_db(archive_db, dry_run)

    logger.info("=== Итого удалено: %d сообщений %s ===", total, "(dry-run)" if dry_run else "")
    if total == 0:
        logger.info("Мусор не найден. Контекст чист.")


if __name__ == "__main__":
    main()
