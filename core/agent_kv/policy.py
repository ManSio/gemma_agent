"""TTL, удаление протухших записей, обрезка истории по лимиту."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from core.agent_kv.store import _db, _lock, agent_kv_enabled, default_kv_path

logger = logging.getLogger(__name__)


def _db_total_bytes(db_path: str) -> int:
    p = Path(db_path)
    total = 0
    for suf in ("", "-wal", "-shm"):
        fp = Path(str(p) + suf)
        try:
            if fp.is_file():
                total += int(fp.stat().st_size)
        except Exception:
            continue
    return total


def sweep_agent_kv() -> Dict[str, int]:
    """
    Удаляет истёкшие ключи, обрезает kv_history по AGENT_KV_MAX_HISTORY_PER_KEY.
    Вызывать периодически (например из maintenance) или редко после записей.
    """
    if not agent_kv_enabled():
        return {"expired": 0, "history_trimmed": 0}
    now = datetime.now(timezone.utc).isoformat()
    expired = 0
    trimmed = 0
    try:
        max_rows = max(1000, int((os.getenv("AGENT_KV_HISTORY_TABLE_MAX_ROWS") or "200000").strip() or "200000"))
    except ValueError:
        max_rows = 200000
    size_before = _db_total_bytes(default_kv_path())
    size_after = size_before
    size_trimmed = 0
    with _lock:
        db = _db()
        cur = db.execute(
            "DELETE FROM kv_store WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        expired = cur.rowcount or 0
        trimmed = 0
        cnt = db.execute("SELECT COUNT(1) AS c FROM kv_history").fetchone()
        n = int(cnt["c"]) if cnt else 0
        over = n - max_rows
        if over > 0:
            db.execute(
                "DELETE FROM kv_history WHERE id IN (SELECT id FROM kv_history ORDER BY id ASC LIMIT ?)",
                (over,),
            )
            trimmed = over
        try:
            max_mb = int((os.getenv("AGENT_KV_MAX_DB_MB") or "0").strip() or "0")
        except ValueError:
            max_mb = 0
        if max_mb > 0:
            size_limit = max(32 * 1024 * 1024, int(max_mb) * 1024 * 1024)
            size_after = _db_total_bytes(default_kv_path())
            if size_after > size_limit:
                to_drop = max(100, min(5000, int((size_after - size_limit) // 4096)))
                db.execute(
                    "DELETE FROM kv_history WHERE id IN (SELECT id FROM kv_history ORDER BY id ASC LIMIT ?)",
                    (to_drop,),
                )
                size_trimmed = to_drop
                try:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'policy', e, exc_info=True)
                try:
                    db.execute("VACUUM")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'policy', e, exc_info=True)
                size_after = _db_total_bytes(default_kv_path())
    try:
        from core.monitoring import MONITOR

        MONITOR.inc("agent_kv_sweep_total")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'policy', e, exc_info=True)
    return {
        "expired": expired,
        "history_trimmed": int(trimmed + size_trimmed),
        "history_trimmed_by_size": int(size_trimmed),
        "db_size_before_bytes": int(size_before),
        "db_size_after_bytes": int(size_after),
        "path": default_kv_path(),
    }
