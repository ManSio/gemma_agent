from __future__ import annotations

import logging

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_PASSPORT_KEYS: Set[str] = {
    "mission",
    "evolution_vectors",
    "priorities",
    "kpi_targets",
    "stop_rules",
}


logger = logging.getLogger(__name__)

def passport_file_path() -> str:
    p = (os.getenv("DEVELOPMENT_PASSPORT_PATH") or "").strip()
    return p if p else "data/development_passport.json"


def passport_backup_dir() -> Path:
    d = Path(os.getenv("PASSPORT_BACKUP_DIR", "data/passport_backups"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_passport_file() -> Optional[str]:
    """Копия текущего файла паспорта перед изменением (для отката при critical)."""
    path = passport_file_path()
    if not path or not os.path.isfile(path):
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = passport_backup_dir() / f"passport_{ts}.json"
    try:
        shutil.copy2(path, dest)
        return str(dest)
    except OSError:
        return None


def rollback_passport_to_latest_backup() -> Dict[str, Any]:
    """Восстановить файл паспорта из последнего бэкапа."""
    backup_dir = passport_backup_dir()
    files: List[Path] = sorted(backup_dir.glob("passport_*.json"))
    if not files:
        return {"ok": False, "error": "no_backups"}
    latest = files[-1]
    dest = os.path.abspath(passport_file_path())
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.copy2(latest, dest)
    return {"ok": True, "restored_from": str(latest)}


def get_passport_source_info() -> Dict[str, Any]:
    path = passport_file_path()
    return {
        "file_path": path,
        "file_exists": bool(path and os.path.isfile(path)),
        "env_json_set": bool(os.getenv("DEVELOPMENT_PASSPORT_JSON", "").strip()),
    }


def _default_passport() -> Dict[str, Any]:
    return {
        "mission": (
            "Безопасная, адаптивная и эффективная поддержка пользователей ассистентом."
        ),
        "evolution_vectors": [
            "качество рассуждений",
            "скорость ответа",
            "усиление безопасности",
            "персонализация",
            "удобство разработки",
        ],
        "priorities": [
            {"id": "safety_first", "weight": 1.0},
            {"id": "predictability", "weight": 0.9},
            {"id": "latency", "weight": 0.7},
        ],
        "kpi_targets": {
            "planner_fallback_total_max": 30,
            "security_high_risk_total_max": 5,
            "flood_blocked_total_max": 100,
        },
        "stop_rules": [
            "no_routing_contract_changes",
            "no_output_schema_changes",
            "advisory_only_self_improvement",
            "no_auto_patch_without_confirmation",
        ],
    }


def _only_passport_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: d[k] for k in _PASSPORT_KEYS if k in d}


def _load_json_file(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return _only_passport_keys(obj) if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _load_env_passport_dict() -> Dict[str, Any]:
    raw = os.getenv("DEVELOPMENT_PASSPORT_JSON", "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return _only_passport_keys(obj) if isinstance(obj, dict) else {}
    except Exception:
        return {}


def get_development_passport() -> Dict[str, Any]:
    base = _default_passport()
    base.update(_load_env_passport_dict())
    base.update(_load_json_file(passport_file_path()))
    return base


def _merge_passport_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if k == "kpi_targets" and isinstance(v, dict):
            cur = out.get("kpi_targets")
            kt: Dict[str, Any] = dict(cur) if isinstance(cur, dict) else {}
            kt.update(v)
            out["kpi_targets"] = kt
        else:
            out[k] = v
    return out


def validate_passport_structure(d: Dict[str, Any]) -> None:
    if not isinstance(d, dict):
        raise ValueError("passport must be a JSON object")
    unknown = set(d.keys()) - _PASSPORT_KEYS
    if unknown:
        raise ValueError(f"unknown keys: {sorted(unknown)}")
    if "mission" in d and not isinstance(d["mission"], str):
        raise ValueError("mission must be a string")
    if "evolution_vectors" in d:
        ev = d["evolution_vectors"]
        if not isinstance(ev, list) or not all(isinstance(x, str) for x in ev):
            raise ValueError("evolution_vectors must be a list of strings")
    if "priorities" in d:
        pr = d["priorities"]
        if not isinstance(pr, list):
            raise ValueError("priorities must be a list")
        for i, p in enumerate(pr):
            if not isinstance(p, dict):
                raise ValueError(f"priorities[{i}] must be an object")
            pid = p.get("id")
            if not isinstance(pid, str) or not pid.strip():
                raise ValueError(f"priorities[{i}].id must be a non-empty string")
            w = p.get("weight", 1.0)
            if not isinstance(w, (int, float)):
                raise ValueError(f"priorities[{i}].weight must be a number")
    if "kpi_targets" in d:
        kt = d["kpi_targets"]
        if not isinstance(kt, dict):
            raise ValueError("kpi_targets must be an object")
        for kk, vv in kt.items():
            if not isinstance(kk, str):
                raise ValueError("kpi_targets keys must be strings")
            if not isinstance(vv, int) or isinstance(vv, bool):
                raise ValueError(f"kpi_targets[{kk!r}] must be an integer")
    if "stop_rules" in d:
        sr = d["stop_rules"]
        if not isinstance(sr, list) or not all(isinstance(x, str) for x in sr):
            raise ValueError("stop_rules must be a list of strings")


def save_passport_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("patch must be a JSON object")
    unknown = set(patch.keys()) - _PASSPORT_KEYS
    if unknown:
        raise ValueError(f"unknown keys: {sorted(unknown)}")
    validate_passport_structure(patch)
    try:
        from core.recovery_autonomy import backup_before_critical_mutations

        backup_before_critical_mutations("passport_patch")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'development_passport', e, exc_info=True)
    backup_passport_file()
    current = get_development_passport()
    merged = _merge_passport_patch(current, patch)
    validate_passport_structure(merged)
    path = passport_file_path()
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def ensure_default_passport_file() -> None:
    """Создаёт файл паспорта с дефолтами, если нет файла и нет DEVELOPMENT_PASSPORT_JSON (для integrity_ok)."""
    if os.getenv("DEVELOPMENT_PASSPORT_JSON", "").strip():
        return
    path = passport_file_path()
    if not path:
        return
    abs_path = os.path.abspath(path)
    if os.path.isfile(abs_path):
        return
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(_default_passport(), f, ensure_ascii=False, indent=2)
