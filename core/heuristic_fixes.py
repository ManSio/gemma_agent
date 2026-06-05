"""Жёсткие правила по паттернам запроса — без LLM, из config/heuristic_fixes.json."""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _config_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    custom = (os.getenv("HEURISTIC_FIXES_PATH") or "").strip()
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else Path(root) / p
    return Path(root) / "config" / "heuristic_fixes.json"


def heuristic_fixes_enabled() -> bool:
    raw = os.getenv("HEURISTIC_FIXES_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def _load_fixes() -> List[Dict[str, Any]]:
    path = _config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fixes = data.get("fixes") if isinstance(data, dict) else []
        return [f for f in fixes if isinstance(f, dict)]
    except Exception as e:
        logger.warning("heuristic_fixes load: %s", e)
        return []


def match_heuristic_hints(user_text: str, *, intent: str = "") -> List[str]:
    """Список hint-строк для подмешивания в external_hint (макс. 2)."""
    if not heuristic_fixes_enabled():
        return []
    low = (user_text or "").strip().lower()
    if not low:
        return []
    intent_l = (intent or "").strip().lower()
    out: List[str] = []
    for fix in _load_fixes():
        pats = fix.get("patterns") or []
        if not isinstance(pats, list):
            continue
        fix_intent = str(fix.get("intent") or "").strip().lower()
        if fix_intent and intent_l and fix_intent != intent_l:
            continue
        if any(str(p).lower() in low for p in pats if p):
            hint = str(fix.get("hint") or "").strip()
            if hint:
                out.append(f"(HeuristicFix:{fix.get('id', 'rule')}) {hint}")
        if len(out) >= 2:
            break
    return out


def build_heuristic_hint_block(user_text: str, *, intent: str = "") -> str:
    hints = match_heuristic_hints(user_text, intent=intent)
    if not hints:
        return ""
    return "\n".join(hints)
