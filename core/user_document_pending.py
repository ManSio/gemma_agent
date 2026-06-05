"""
Черновики вложений: текст копируется в data/runtime/pending_docs, временный файл Telegram можно сразу удалить.
Колбэки udoc:p / udoc:k / udoc:x — личная библиотека, общая база (по политике), удалить черновик.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


def _env_truthy(name: str, *, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def pending_docs_enabled() -> bool:
    return _env_truthy("USER_DOC_PENDING_ENABLED", default=True)


def shared_knowledge_upload_open() -> bool:
    mode = (os.getenv("SHARED_KNOWLEDGE_UPLOAD") or "admin").strip().lower()
    return mode in {"all", "everyone", "1", "true", "yes", "on"}


def shared_upload_visible(is_admin: bool) -> bool:
    if shared_knowledge_upload_open():
        return True
    return bool(is_admin)


def can_write_shared_knowledge(is_admin: bool) -> bool:
    return shared_upload_visible(is_admin)


def _runtime_dir() -> Path:
    return Path((os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip() or "data/runtime")


def _pending_dir() -> Path:
    return _runtime_dir() / "pending_docs"


def _user_library_root() -> Path:
    return Path((os.getenv("USER_LIBRARY_DIR") or "data/user_library").strip() or "data/user_library")


def _shared_knowledge_root() -> Path:
    return Path((os.getenv("SHARED_KNOWLEDGE_DIR") or "data/shared_knowledge").strip() or "data/shared_knowledge")


def _pending_max_bytes() -> int:
    try:
        return max(50_000, int((os.getenv("PENDING_DOC_MAX_BYTES") or "2500000").strip()))
    except ValueError:
        return 2_500_000


def _valid_pid(pid: str) -> bool:
    s = (pid or "").strip().lower()
    if len(s) < 8 or len(s) > 32:
        return False
    return bool(re.fullmatch(r"[0-9a-f]+", s))


def _safe_disk_stem(name: str) -> str:
    base = (name or "document").replace("\\", "/").split("/")[-1].strip() or "document"
    if "." in base:
        base = base.rsplit(".", 1)[0]
    out: List[str] = []
    for c in base[:160]:
        if c.isalnum() or c in "._- ":
            out.append(c)
        else:
            out.append("_")
    s = "".join(out).strip("._- ") or "document"
    return s[:180]


def _unique_path(dir_path: Path, stem: str, suffix: str = ".txt") -> Path:
    base = dir_path / f"{stem}{suffix}"
    if not base.exists():
        return base
    for i in range(2, 5000):
        cand = dir_path / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    return dir_path / f"{stem}_{secrets.token_hex(4)}{suffix}"


def register_pending_if_enabled(
    *,
    user_id: str,
    chat_id: str,
    filename: str,
    body: str,
) -> Optional[str]:
    if not pending_docs_enabled():
        return None
    raw = (body or "").encode("utf-8")
    if not raw.strip():
        return None
    lim = _pending_max_bytes()
    if len(raw) > lim:
        return None
    pid = secrets.token_hex(8)
    pdir = _pending_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "user_id": str(user_id),
        "chat_id": str(chat_id),
        "filename": str(filename or "document")[:500],
        "created_unix": time.time(),
    }
    meta_path = pdir / f"{pid}.meta.json"
    txt_path = pdir / f"{pid}.txt"
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=0), encoding="utf-8")
        txt_path.write_bytes(raw)
    except OSError:
        try:
            if meta_path.is_file():
                meta_path.unlink()
        except OSError:
            pass
        try:
            if txt_path.is_file():
                txt_path.unlink()
        except OSError:
            pass
        return None
    return pid


def load_pending_meta(pid: str) -> Optional[Dict[str, Any]]:
    if not _valid_pid(pid):
        return None
    meta_path = _pending_dir() / f"{pid}.meta.json"
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_pending_body(pid: str) -> str:
    if not _valid_pid(pid):
        return ""
    txt_path = _pending_dir() / f"{pid}.txt"
    if not txt_path.is_file():
        return ""
    try:
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def delete_pending(pid: str) -> None:
    if not _valid_pid(pid):
        return
    pdir = _pending_dir()
    for name in (f"{pid}.meta.json", f"{pid}.txt"):
        p = pdir / name
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


def pending_document_keyboard_rows(pending_id: str) -> List[List[Dict[str, str]]]:
    """Три кнопки в столбик: личное хранилище, общая база (запись только у кого положено по политике), удалить."""
    pid = pending_id.strip().lower()
    if not _valid_pid(pid):
        return []
    return [
        [{"text": "🔒 Личное", "callback_data": f"udoc:p:{pid}"}],
        [{"text": "📚 Общая база", "callback_data": f"udoc:k:{pid}"}],
        [{"text": "🗑 Удалить черновик", "callback_data": f"udoc:x:{pid}"}],
    ]


def _save_personal_library(user_id: str, original_name: str, body: str) -> str:
    root = _user_library_root() / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    stem = _safe_disk_stem(original_name)
    path = _unique_path(root, stem, ".txt")
    path.write_text(body, encoding="utf-8", newline="\n")
    return str(path)


def _save_shared_knowledge(user_id: str, original_name: str, body: str, pending_id: str) -> str:
    root = _shared_knowledge_root() / "ingest"
    root.mkdir(parents=True, exist_ok=True)
    stem = _safe_disk_stem(original_name)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = f"{day}_{user_id}_{pending_id[:8]}_{stem}"
    path = _unique_path(root, base, ".txt")
    header = (
        f"# shared_knowledge ingest\n"
        f"# user_id: {user_id}\n"
        f"# source_file: {original_name}\n"
        f"# pending_id: {pending_id}\n"
        f"# saved_utc: {datetime.now(timezone.utc).isoformat()}\n\n"
    )
    path.write_text(header + body, encoding="utf-8", newline="\n")
    return str(path)


async def handle_udoc_callback(layer: Any, callback: CallbackQuery) -> None:
    data = (getattr(callback, "data", None) or "").strip()
    uid = str(callback.from_user.id)
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "udoc":
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    action, pid = parts[1].strip().lower(), parts[2].strip().lower()
    if action not in {"p", "k", "x"}:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    if not _valid_pid(pid):
        await callback.answer("Некорректный идентификатор", show_alert=True)
        return

    meta = load_pending_meta(pid)
    if not meta:
        await callback.answer("Черновик устарел или уже обработан", show_alert=True)
        return
    owner = str(meta.get("user_id") or "")
    if owner != uid:
        await callback.answer("Это вложение оформлено другим пользователем", show_alert=True)
        return

    body = load_pending_body(pid)
    if not body.strip():
        delete_pending(pid)
        await callback.answer("Текст черновика пуст — запись удалена", show_alert=True)
        return

    orig_name = str(meta.get("filename") or "document")
    is_admin = False
    try:
        is_admin = bool(layer._admin_module.is_admin(uid))
    except Exception:
        is_admin = False

    chat_id = getattr(getattr(callback.message, "chat", None), "id", None)

    async def _notify(text: str) -> None:
        if chat_id is None:
            return
        try:
            await callback.bot.send_message(int(chat_id), text)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'user_document_pending', e, exc_info=True)
    if action == "x":
        delete_pending(pid)
        await callback.answer("Черновик удалён")
        await _notify("Черновик вложения удалён (временные данные стёрты).")
        return

    if action == "k":
        if not can_write_shared_knowledge(is_admin):
            await callback.answer("Добавлять в общую базу могут только администраторы", show_alert=True)
            return
        path = _save_shared_knowledge(uid, orig_name, body, pid)
        try:
            from core.document_corpus_store import register_shared_knowledge_ingest

            register_shared_knowledge_ingest(
                pending_id=pid,
                user_id=uid,
                original_name=orig_name,
                body=body,
                saved_path=path,
            )
        except Exception as e:
            logger.warning("[shared_knowledge] corpus index: %s", e)
        delete_pending(pid)
        await callback.answer("Сохранено в общую базу")
        await _notify(
            "Текст добавлен в общую базу знаний (ingest):\n"
            f"`{path}`\n\n"
            "Запись проиндексирована в **DocumentCorpus** (поиск: **DocumentCorpus.unified_search** / **DocumentCorpus.stats**)."
        )
        return

    if action == "p":
        path = _save_personal_library(uid, orig_name, body)
        delete_pending(pid)
        await callback.answer("Сохранено в личную библиотеку")
        await _notify(f"Файл в личной библиотеке:\n`{path}`")
        return

    await callback.answer("Неизвестное действие", show_alert=True)
