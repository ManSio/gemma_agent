"""Slash /mem_* ↔ Mem0 (единый источник для brain; slash-store — fallback)."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.encrypted_json_store import read_encrypted_json, write_encrypted_json

logger = logging.getLogger(__name__)


def _slash_facts_path(storage_path: str) -> Path:
    return Path(storage_path) / "facts.json"


def _load_slash_facts(path: Path) -> List[Dict[str, Any]]:
    raw = read_encrypted_json(path, [])
    return raw if isinstance(raw, list) else []


def _mem0_configured() -> bool:
    try:
        from core.mem0_memory.mem0_module import load_mem0_config_from_env

        return bool(load_mem0_config_from_env())
    except Exception:
        return False


def _mem0_lines(user_id: str, query: Optional[str] = None) -> List[str]:
    uid = str(user_id or "").strip()
    if not uid or not _mem0_configured():
        return []
    try:
        from core.brain.runtime import get_memory

        rows = get_memory().get_facts(uid, query=query)
        out: List[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            text = (
                row.get("content")
                or row.get("memory")
                or row.get("text")
                or ""
            )
            text = str(text).strip()
            if text:
                out.append(text)
        return out
    except Exception:
        logger.exception("[memory_slash_bridge] mem0 read failed uid=%s", uid)
        return []


def remember_fact(
    user_id: Optional[str],
    fact: str,
    storage_path: str,
) -> Tuple[bool, str]:
    """
    Запись: Mem0 (если user_id + конфиг), иначе slash-store.
    При успехе Mem0 slash-store тоже обновляется (офлайн-резерв).
    """
    fact = (fact or "").strip()
    if not fact:
        return False, "empty"
    uid = str(user_id or "").strip()
    path = _slash_facts_path(storage_path)
    mem_ok = False
    if uid and _mem0_configured():
        try:
            from core.brain.runtime import get_memory

            get_memory().add_structured_facts(
                uid,
                [{"field": "user_fact", "content": fact}],
            )
            mem_ok = True
        except Exception:
            logger.exception("[memory_slash_bridge] mem0 remember failed uid=%s", uid)
    slash_ok = _append_slash_fact(path, fact)
    if mem_ok:
        return True, "mem0"
    if slash_ok:
        return True, "slash_only"
    return False, "failed"


def recall_facts(
    user_id: Optional[str],
    storage_path: str,
    *,
    limit: int = 10,
) -> Tuple[List[str], str]:
    """Сначала Mem0, затем slash без дубликатов."""
    uid = str(user_id or "").strip()
    seen: set[str] = set()
    merged: List[str] = []
    backend_parts: List[str] = []

    if uid and _mem0_configured():
        for line in _mem0_lines(uid):
            key = line.lower()
            if key not in seen:
                seen.add(key)
                merged.append(line)
        if merged:
            backend_parts.append("mem0")

    for item in _load_slash_facts(_slash_facts_path(storage_path)):
        if not isinstance(item, dict):
            continue
        line = str(item.get("fact") or "").strip()
        if not line:
            continue
        key = line.lower()
        if key not in seen:
            seen.add(key)
            merged.append(line)
    if _load_slash_facts(_slash_facts_path(storage_path)) and "slash" not in backend_parts:
        backend_parts.append("slash")

    if not backend_parts:
        backend_parts.append("slash" if merged else "empty")
    return merged[-limit:], "+".join(backend_parts) or "empty"


def forget_fact(
    user_id: Optional[str],
    fact: str,
    storage_path: str,
) -> Tuple[bool, str, int]:
    """Slash-store + Mem0 delete по совпадению текста. Возвращает (ok, backend, mem0_deleted)."""
    fact = (fact or "").strip()
    path = _slash_facts_path(storage_path)
    facts = _load_slash_facts(path)
    new_facts = [f for f in facts if isinstance(f, dict) and f.get("fact") != fact]
    slash_ok = len(new_facts) < len(facts)
    if slash_ok:
        write_encrypted_json(path, new_facts)
    uid = str(user_id or "").strip()
    mem_deleted = 0
    if uid and _mem0_configured():
        try:
            from core.brain.runtime import get_memory

            mem_deleted = get_memory().delete_facts_matching_text(uid, fact)
        except Exception:
            logger.exception("[memory_slash_bridge] mem0 forget failed uid=%s", uid)
    if mem_deleted > 0 and slash_ok:
        return True, "mem0+slash", mem_deleted
    if mem_deleted > 0:
        return True, "mem0", mem_deleted
    if slash_ok:
        return True, "slash", 0
    return False, "not_found", 0


def _append_slash_fact(path: Path, fact: str) -> bool:
    facts = _load_slash_facts(path)
    facts.append({"fact": fact, "timestamp": datetime.now().isoformat()})
    return write_encrypted_json(path, facts)
