"""Маршрутные примеры для probe/corpus (без stop-words)."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "data/learning/route_examples.jsonl"
_VALID_PROFILE = re.compile(r"^[a-z][a-z0-9_]{0,48}$")


def route_examples_path() -> Path:
    root = Path((os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or ".")
    custom = (os.getenv("ROUTE_EXAMPLES_PATH") or "").strip()
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else root / p
    return root / _DEFAULT_PATH


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip():
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return rows


def load_route_examples() -> List[Dict[str, Any]]:
    return _read_jsonl(route_examples_path())


def append_route_example(
    *,
    text: str,
    expected_profile: str,
    added_by: str = "",
    tags: Optional[List[str]] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Добавить route_only кейс в JSONL (дедуп по fingerprint текста)."""
    from core.experience_memory import fingerprint

    body = (text or "").strip()
    prof = (expected_profile or "").strip().lower()
    if len(body) < 3:
        raise ValueError("text_too_short")
    if not _VALID_PROFILE.match(prof):
        raise ValueError("invalid_profile")
    fp = fingerprint(body)
    path = route_examples_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_jsonl(path)
    for row in existing:
        if str(row.get("fp") or "") == fp:
            raise ValueError("duplicate_text")
    case_id = f"route_ex_{fp[:10]}"
    rec: Dict[str, Any] = {
        "id": case_id,
        "fp": fp,
        "source": "route_example",
        "route_only": True,
        "text": body,
        "validators": ["check_preflight_profile"],
        "expect_preflight_profile": prof,
        "tags": list(tags or []) + ["reform", "owner_example"],
        "expected_profile": prof,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": (added_by or "")[:64],
        "note": (note or "")[:240],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("route_example appended id=%s profile=%s", case_id, prof)
    return rec


def route_example_to_corpus_case(row: Dict[str, Any]) -> Dict[str, Any]:
    """Строка JSONL → кейс build_test_corpus / agent_test."""
    text = str(row.get("text") or "").strip()
    prof = str(row.get("expected_profile") or row.get("expect_preflight_profile") or "").strip()
    cid = str(row.get("id") or f"route_ex_{row.get('fp', '')[:10]}")
    return {
        "id": cid,
        "source": "route_example",
        "route_only": True,
        "text": text,
        "validators": ["check_preflight_profile"],
        "expect_preflight_profile": prof,
        "tags": row.get("tags") or ["reform", "owner_example"],
    }
