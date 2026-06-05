"""
SQLite KV с ветками, версиями, TTL, приоритетами и историей для rollback.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_conn_holder: Dict[str, sqlite3.Connection] = {}


def agent_kv_enabled() -> bool:
    """Включите AGENT_KV_ENABLED=true для персистентного KV (SQLite). По умолчанию выкл., чтобы не трогать диск в тестах."""
    return effective_bool("AGENT_KV_ENABLED", default=False)


def default_kv_path() -> str:
    p = (os.getenv("AGENT_KV_SQLITE_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "agent_kv.sqlite3")


def agent_kv_branch() -> str:
    b = (os.getenv("AGENT_KV_BRANCH") or "main").strip() or "main"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in b)[:64]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn_for_path(path: str) -> sqlite3.Connection:
    with _lock:
        if path not in _conn_holder:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            _init_schema(c)
            _conn_holder[path] = c
        return _conn_holder[path]


def _init_schema(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kv_store (
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT 'main',
            value TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            PRIMARY KEY (namespace, key, branch)
        );
        CREATE TABLE IF NOT EXISTS kv_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            branch TEXT NOT NULL,
            version INTEGER NOT NULL,
            value TEXT NOT NULL,
            saved_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv_store(expires_at);
        CREATE INDEX IF NOT EXISTS idx_hist_nkb ON kv_history(namespace, key, branch, version DESC);
        CREATE TABLE IF NOT EXISTS kv_branches (
            name TEXT PRIMARY KEY,
            forked_from TEXT,
            forked_at TEXT NOT NULL
        );
        """
    )


def _db() -> sqlite3.Connection:
    return _conn_for_path(default_kv_path())


def get_json(
    namespace: str,
    key: str,
    *,
    branch: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not agent_kv_enabled():
        return None
    br = branch if branch is not None else agent_kv_branch()
    with _lock:
        row = _db().execute(
            "SELECT value, expires_at FROM kv_store WHERE namespace=? AND key=? AND branch=?",
            (namespace, key, br),
        ).fetchone()
    if not row:
        return None
    exp = row["expires_at"]
    if exp:
        try:
            if datetime.fromisoformat(str(exp).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return None
        except Exception as e:
            logger.debug('%s optional failed: %s', 'store', e, exc_info=True)
    try:
        o = json.loads(row["value"])
        return o if isinstance(o, dict) else {"_raw": o}
    except json.JSONDecodeError:
        return None


def set_json(
    namespace: str,
    key: str,
    value: Dict[str, Any],
    *,
    branch: Optional[str] = None,
    ttl_sec: Optional[int] = None,
    priority: int = 0,
) -> int:
    if not agent_kv_enabled():
        return 0
    br = branch if branch is not None else agent_kv_branch()
    payload = json.dumps(value, ensure_ascii=False)
    now = _now_iso()
    exp: Optional[str] = None
    if ttl_sec is not None and ttl_sec > 0:
        from datetime import timedelta

        exp = (datetime.now(timezone.utc) + timedelta(seconds=float(ttl_sec))).isoformat()
    with _lock:
        db = _db()
        row = db.execute(
            "SELECT version FROM kv_store WHERE namespace=? AND key=? AND branch=?",
            (namespace, key, br),
        ).fetchone()
        ver = int(row["version"]) + 1 if row else 1
        if row:
            db.execute(
                """UPDATE kv_store SET value=?, version=?, priority=?, updated_at=?, expires_at=?
                   WHERE namespace=? AND key=? AND branch=?""",
                (payload, ver, int(priority), now, exp, namespace, key, br),
            )
        else:
            db.execute(
                """INSERT INTO kv_store(namespace,key,branch,value,version,priority,created_at,updated_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (namespace, key, br, payload, ver, int(priority), now, now, exp),
            )
        db.execute(
            "INSERT INTO kv_history(namespace,key,branch,version,value,saved_at) VALUES(?,?,?,?,?,?)",
            (namespace, key, br, ver, payload, now),
        )
    return ver


def delete_key(namespace: str, key: str, *, branch: Optional[str] = None) -> None:
    if not agent_kv_enabled():
        return
    br = branch if branch is not None else agent_kv_branch()
    with _lock:
        _db().execute("DELETE FROM kv_store WHERE namespace=? AND key=? AND branch=?", (namespace, key, br))


def iter_prefix(
    namespace: str,
    key_prefix: str,
    *,
    branch: Optional[str] = None,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    if not agent_kv_enabled():
        yield from ()
        return
    br = branch if branch is not None else agent_kv_branch()
    pat = f"{key_prefix}%"
    with _lock:
        cur = _db().execute(
            "SELECT key, value, expires_at FROM kv_store WHERE namespace=? AND branch=? AND key LIKE ?",
            (namespace, br, pat),
        )
        rows = cur.fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        exp = row["expires_at"]
        if exp:
            try:
                if datetime.fromisoformat(str(exp).replace("Z", "+00:00")) < now:
                    continue
            except Exception as e:
                logger.debug('%s optional failed: %s', 'store', e, exc_info=True)
        try:
            o = json.loads(row["value"])
            if isinstance(o, dict):
                yield row["key"], o
        except json.JSONDecodeError:
            continue


def get_history(
    namespace: str,
    key: str,
    *,
    branch: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    if not agent_kv_enabled():
        return []
    br = branch if branch is not None else agent_kv_branch()
    lim = max(1, min(200, int(limit)))
    with _lock:
        rows = _db().execute(
            """SELECT version, value, saved_at FROM kv_history
               WHERE namespace=? AND key=? AND branch=? ORDER BY version DESC LIMIT ?""",
            (namespace, key, br, lim),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            v = json.loads(r["value"])
        except json.JSONDecodeError:
            v = {}
        out.append({"version": int(r["version"]), "saved_at": r["saved_at"], "value": v})
    return out


def rollback_to_version(
    namespace: str,
    key: str,
    target_version: int,
    *,
    branch: Optional[str] = None,
) -> bool:
    if not agent_kv_enabled():
        return False
    br = branch if branch is not None else agent_kv_branch()
    with _lock:
        db = _db()
        row = db.execute(
            """SELECT value FROM kv_history WHERE namespace=? AND key=? AND branch=? AND version=?""",
            (namespace, key, br, int(target_version)),
        ).fetchone()
        if not row:
            return False
        payload = row["value"]
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return False
        if not isinstance(obj, dict):
            return False
        now = _now_iso()
        cur = db.execute(
            "SELECT version FROM kv_store WHERE namespace=? AND key=? AND branch=?",
            (namespace, key, br),
        ).fetchone()
        ver = int(cur["version"]) + 1 if cur else int(target_version)
        if cur:
            db.execute(
                """UPDATE kv_store SET value=?, version=?, updated_at=? WHERE namespace=? AND key=? AND branch=?""",
                (payload, ver, now, namespace, key, br),
            )
        else:
            db.execute(
                """INSERT INTO kv_store(namespace,key,branch,value,version,priority,created_at,updated_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,NULL)""",
                (namespace, key, br, payload, ver, 0, now, now),
            )
        db.execute(
            "INSERT INTO kv_history(namespace,key,branch,version,value,saved_at) VALUES(?,?,?,?,?,?)",
            (namespace, key, br, ver, payload, now),
        )
    return True


def copy_branch(from_branch: str, to_branch: str) -> None:
    if not agent_kv_enabled():
        return
    fb = (from_branch or "main").strip() or "main"
    tb = (to_branch or "fork").strip() or "fork"
    now = _now_iso()
    with _lock:
        db = _db()
        db.execute(
            "INSERT OR REPLACE INTO kv_branches(name, forked_from, forked_at) VALUES(?,?,?)",
            (tb, fb, now),
        )
        rows = db.execute(
            "SELECT namespace, key, value, version, priority, created_at, updated_at, expires_at FROM kv_store WHERE branch=?",
            (fb,),
        ).fetchall()
        for r in rows:
            db.execute(
                """INSERT OR REPLACE INTO kv_store(namespace,key,branch,value,version,priority,created_at,updated_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    r["namespace"],
                    r["key"],
                    tb,
                    r["value"],
                    r["version"],
                    r["priority"],
                    r["created_at"],
                    now,
                    r["expires_at"],
                ),
            )


def reset_agent_kv_connection_cache() -> None:
    """Для тестов: сбросить соединения после смены пути к БД."""
    global _conn_holder
    with _lock:
        for _p, c in list(_conn_holder.items()):
            try:
                c.close()
            except Exception as e:
                logger.debug('%s optional failed: %s', 'store', e, exc_info=True)
        _conn_holder.clear()


def list_branches() -> List[str]:
    if not agent_kv_enabled():
        return ["main"]
    with _lock:
        rows = _db().execute("SELECT name FROM kv_branches ORDER BY name").fetchall()
    names = [r["name"] for r in rows]
    if "main" not in names:
        names.insert(0, "main")
    return names


def integrity_check() -> Dict[str, Any]:
    """Проверка целостности SQLite KV-хранилища: PRAGMA quick_check + попытка чтения всех строк."""
    if not agent_kv_enabled():
        return {"ok": True, "enabled": False}
    result: Dict[str, Any] = {"ok": True, "enabled": True}
    path = default_kv_path()
    result["path"] = path
    if not os.path.isfile(path):
        result["ok"] = True
        result["note"] = "kv file not created yet"
        return result
    with _lock:
        try:
            db = _db()
            row = db.execute("PRAGMA quick_check").fetchone()
            quick = str(row[0]) if row else ""
            result["quick_check"] = quick
            if quick.lower() != "ok":
                result["ok"] = False
                result["error"] = f"PRAGMA quick_check: {quick}"
                return result
        except sqlite3.DatabaseError as e:
            result["ok"] = False
            result["error"] = f"PRAGMA quick_check failed: {e}"
            return result
        try:
            row = db.execute("PRAGMA page_count").fetchone()
            result["page_count"] = int(row[0]) if row else 0
        except sqlite3.DatabaseError as e:
            result["ok"] = False
            result["error"] = f"PRAGMA page_count failed: {e}"
            return result
        try:
            count_row = db.execute("SELECT COUNT(*) FROM kv_store").fetchone()
            kv_count = int(count_row[0]) if count_row else 0
            result["kv_rows"] = kv_count
            count_h = db.execute("SELECT COUNT(*) FROM kv_history").fetchone()
            result["history_rows"] = int(count_h[0]) if count_h else 0
        except sqlite3.DatabaseError as e:
            result["ok"] = False
            result["error"] = f"row count failed: {e}"
            return result
    return result


def repair_kv_store() -> Dict[str, Any]:
    """Если integrity_check показал проблему — бэкап, удаление, пересоздание."""
    if not agent_kv_enabled():
        return {"ok": True, "repaired": False, "note": "kv disabled"}
    check = integrity_check()
    if check.get("ok"):
        return {"ok": True, "repaired": False, "check": check}
    path = default_kv_path()
    bak = path + ".corrupt." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".bak"
    try:
        if os.path.isfile(path):
            os.rename(path, bak)
        reset_agent_kv_connection_cache()
        # re-init will happen on next access
        return {"ok": True, "repaired": True, "backup": bak, "check": check}
    except OSError as e:
        return {"ok": False, "error": f"repair failed: {e}", "check": check}
