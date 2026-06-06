"""
Правила оператора без правки кода: JSON на диске (volume / SFTP).

Файл по умолчанию: data/runtime/operator_rules.json
Переопределение: OPERATOR_RULES_PATH=/abs/path/rules.json

Используется маршрутизацией (intent math → general) и может добавлять хинт в контекст мозга.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List

_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_cache_mtime: float = 0.0

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path(__file__).resolve().parent.parent


def _default_rules_path() -> Path:
    raw = (os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _repo_root() / p
    return (p / "operator_rules.json").resolve()


def rules_path() -> Path:
    env = (os.getenv("OPERATOR_RULES_PATH") or "").strip()
    if env:
        pp = Path(env)
        return pp.resolve() if pp.is_absolute() else (_repo_root() / pp).resolve()
    return _default_rules_path()


def _load_unlocked() -> Dict[str, Any]:
    path = rules_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("operator_rules: cannot load %s: %s", path, e)
        return {}


def invalidate_operator_rules_cache() -> None:
    """После замены файла на диске — сбросить кэш, чтобы перечитать."""
    global _cache, _cache_mtime
    with _lock:
        _cache = {}
        _cache_mtime = 0.0


def load_operator_rules() -> Dict[str, Any]:
    global _cache, _cache_mtime
    path = rules_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    with _lock:
        if mtime == _cache_mtime and _cache:
            return dict(_cache)
        data = _load_unlocked()
        _cache = dict(data)
        _cache_mtime = mtime
        return dict(_cache)


def snapshot_for_operator() -> Dict[str, Any]:
    path = rules_path()
    data = load_operator_rules()
    patterns = data.get("force_general_when_text_matches") or []
    if not isinstance(patterns, list):
        patterns = []
    return {
        "path": str(path),
        "exists": path.is_file(),
        "prefer_general_over_math_globally": bool(data.get("prefer_general_over_math_globally")),
        "force_general_patterns_count": len(patterns),
        "has_brain_addon": bool(str(data.get("brain_context_addon") or "").strip()),
        "version": data.get("version"),
    }


def prefer_general_over_math_from_file() -> bool:
    return bool(load_operator_rules().get("prefer_general_over_math_globally"))


def brain_context_addon_from_file() -> str:
    return str(load_operator_rules().get("brain_context_addon") or "").strip()


def force_general_intent_by_operator_patterns(text: str) -> bool:
    raw = text or ""
    data = load_operator_rules()
    patterns = data.get("force_general_when_text_matches") or []
    if not isinstance(patterns, list):
        return False
    for p in patterns:
        if not isinstance(p, str) or not p.strip():
            continue
        try:
            if re.search(p, raw, re.IGNORECASE):
                return True
        except re.error:
            logger.warning("operator_rules: invalid regex, skip: %r", p[:80])
    return False
