"""Загрузка реестра shortcut-правил из config/heuristic_shortcuts.json."""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def shortcuts_freeze_new_rules() -> bool:
    """Не принимать новые rule id из .local.json (реформа фаза 0)."""
    raw = (os.getenv("HEURISTIC_SHORTCUTS_FREEZE") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _config_dir() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "config"


def _load_rules_file(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules") if isinstance(data, dict) else []
        out: Dict[str, Dict[str, Any]] = {}
        for row in rules if isinstance(rules, list) else []:
            if isinstance(row, dict) and row.get("id"):
                out[str(row["id"])] = row
        return out
    except Exception as e:
        logger.warning("heuristic_shortcuts load %s: %s", path, e)
        return {}


def _merge_rule(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, val in override.items():
        if key == "negative_patterns":
            continue
        if val is not None:
            merged[key] = val
    bp = [str(x).strip() for x in (base.get("negative_patterns") or []) if str(x).strip()]
    op = [str(x).strip() for x in (override.get("negative_patterns") or []) if str(x).strip()]
    if bp or op:
        merged["negative_patterns"] = list(dict.fromkeys(bp + op))
    return merged


@lru_cache(maxsize=1)
def load_shortcut_rules() -> Dict[str, Dict[str, Any]]:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    cfg = _config_dir()
    base_path = cfg / "heuristic_shortcuts.json"
    custom = (os.getenv("HEURISTIC_SHORTCUTS_PATH") or "").strip()
    if custom:
        cp = Path(custom)
        base_path = cp if cp.is_absolute() else Path(root) / cp
    local_path = cfg / "heuristic_shortcuts.local.json"
    custom_local = (os.getenv("HEURISTIC_SHORTCUTS_LOCAL_PATH") or "").strip()
    if custom_local:
        lp = Path(custom_local)
        local_path = lp if lp.is_absolute() else Path((os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or ".") / lp

    out = _load_rules_file(base_path)
    base_ids = set(out.keys())
    local = _load_rules_file(local_path)
    freeze = shortcuts_freeze_new_rules()
    for rid, row in local.items():
        if freeze and rid not in base_ids:
            logger.warning(
                "HEURISTIC_SHORTCUTS_FREEZE: ignore new shortcut rule id=%s (use corpus/route_examples)",
                rid,
            )
            continue
        if rid in out:
            out[rid] = _merge_rule(out[rid], row)
        else:
            out[rid] = row
    return out


def get_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    return load_shortcut_rules().get(str(rule_id or "").strip())


def registry_reload() -> None:
    load_shortcut_rules.cache_clear()
