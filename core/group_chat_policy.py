from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

# participate_mode:
#   mention — только @бот, реплай на бота, /команды (остальное в group_transcript)
#   balanced — mention/reply/команды + открытый вопрос в чате + имя бота без @
#   active — ответ на каждое сообщение (шумно в жарких группах)
_DEFAULTS: Dict[str, Any] = {
    "active_mode": False,
    "group_memory_max": 12,
}


def _normalize_participate_mode(raw: Dict[str, Any]) -> str:
    if bool(raw.get("active_mode")):
        return "active"
    explicit = raw.get("participate_mode")
    if explicit is not None and str(explicit).strip():
        mode = str(explicit).strip().lower()
        if mode in {"mention", "listen", "passive"}:
            return "mention"
        if mode in {"balanced", "smart", "questions"}:
            return "balanced"
        if mode in {"active", "on", "all"}:
            return "active"
    return "mention"


def _apply_policy_fields(out: Dict[str, Any]) -> Dict[str, Any]:
    mode = _normalize_participate_mode(out)
    out["participate_mode"] = mode
    out["active_mode"] = mode == "active"
    try:
        out["group_memory_max"] = max(4, min(40, int(out.get("group_memory_max", 12))))
    except (TypeError, ValueError):
        out["group_memory_max"] = 12
    return out


def _path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "group_chat_policy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_group_chat_policy() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return _apply_policy_fields(dict(_DEFAULTS))
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return _apply_policy_fields(dict(_DEFAULTS))
    if not isinstance(raw, dict):
        return _apply_policy_fields(dict(_DEFAULTS))
    out = dict(_DEFAULTS)
    out.update(raw)
    return _apply_policy_fields(out)


def save_group_chat_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    cur = load_group_chat_policy()
    cur.update(policy or {})
    cur = _apply_policy_fields(cur)
    _path().write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur
