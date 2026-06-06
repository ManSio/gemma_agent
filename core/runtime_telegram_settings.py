"""
Переключатели для админа из Telegram без правки .env на ПК.

Хранятся в data/runtime/admin_telegram_settings.json и имеют приоритет над
переменными окружения для поддерживаемых ключей (пока только bool).

Секреты сюда не кладём — только безопасные UX-флаги.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_cache_mtime: float = 0.0

# (callback_id, env_key, короткая подпись кнопки, дефолт если нет store и нет env)
TOGGLE_DEFS: List[Tuple[str, str, str, bool]] = [
    ("wfb", "BRAIN_WEATHER_URLFETCH_FALLBACK", "Погода: запасной wttr.in", True),
    ("vlo", "VOICE_STT_LOCAL_ONLY", "STT только локально", False),
    ("vrb", "VOICE_STT_AUTO_OPENROUTER_FALLBACK", "STT: запасной OpenRouter", True),
    ("rex", "OPENROUTER_EXPOSE_REASONING", "Reasoning в ответе LLM", False),
    ("asr", "TELEGRAM_ADMIN_STREAM_REASONING", "Stream: CoT в TG (админ)", False),
    # Мозг / стратегия (без секретов; приоритет над .env — см. effective_bool)
    ("sls", "STRATEGIC_LENSES_HINT_ENABLED", "Мозг: StrategicLenses", True),
    ("lkp", "LOOKAHEAD_PLANNER_ENABLED", "План lookahead (без 2-го LLM)", True),
    ("slo", "STRATEGY_LLM_OUTLINE_ENABLED", "Мозг: JSON-outline задачи", True),
    ("sla", "STRATEGY_LLM_OUTLINE_ALWAYS", "Outline для любого текста", False),
    ("rrh", "ROUTE_RISK_HINT_ENABLED", "Подсказка RouteRisk", True),
    ("rrm", "ROUTE_RISK_MEMORY_ENABLED", "Журнал RouteRisk (stumble)", True),
    ("spm", "STRATEGY_PATH_MEMORY_ENABLED", "Память strategy_path", True),
    ("sps", "STRATEGY_PATH_HINT_FOR_SHALLOW", "Strategy path и для shallow", False),
]

DEFAULTS = {env: d for _, env, _, d in TOGGLE_DEFS}
TOGGLE_BY_ID: Dict[str, Tuple[str, str, bool]] = {
    tid: (env, title, d) for tid, env, title, d in TOGGLE_DEFS
}


def _repo_root() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path(__file__).resolve().parent.parent


def _store_path() -> Path:
    raw = (os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _repo_root() / p
    return (p / "admin_telegram_settings.json").resolve()


def _load_store_unlocked() -> Dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_store() -> Dict[str, Any]:
    global _cache, _cache_mtime
    path = _store_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    with _lock:
        if mtime == _cache_mtime and _cache:
            return dict(_cache)
        data = _load_store_unlocked()
        _cache = dict(data)
        _cache_mtime = mtime
        return dict(_cache)


def _invalidate_cache() -> None:
    global _cache, _cache_mtime
    with _lock:
        _cache = {}
        _cache_mtime = 0.0


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "on"}


def effective_bool(env_key: str, *, default: bool) -> bool:
    """store → os.environ → default."""
    store = _load_store()
    if env_key in store:
        return _coerce_bool(store[env_key])
    raw = os.getenv(env_key)
    if raw is not None and str(raw).strip() != "":
        return _coerce_bool(raw)
    return default


def set_override(env_key: str, value: bool) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = _load_store_unlocked()
        data[env_key] = bool(value)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    _invalidate_cache()


def toggle(env_key: str, *, default: bool) -> bool:
    cur = effective_bool(env_key, default=default)
    new = not cur
    set_override(env_key, new)
    return new


def toggle_by_id(toggle_id: str) -> Tuple[str, bool] | None:
    """Возвращает (env_key, new_value) или None если id неизвестен."""
    t = (toggle_id or "").strip().lower()
    item = TOGGLE_BY_ID.get(t)
    if not item:
        return None
    env_key, _title, dflt = item
    new = toggle(env_key, default=dflt)
    return env_key, new


def snapshot_for_operator() -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": str(_store_path()), "overrides": {}}
    store = _load_store()
    for tid, env_key, title, dflt in TOGGLE_DEFS:
        out["overrides"][env_key] = {
            "toggle_id": tid,
            "title": title,
            "effective": effective_bool(env_key, default=dflt),
            "in_store": env_key in store,
        }
    return out
