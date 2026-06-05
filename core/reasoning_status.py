from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def _status_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "reasoning_bench_last.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _quality_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "reasoning_quality_last.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_reasoning_bench_snapshot(payload: Dict[str, Any]) -> None:
    _status_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_reasoning_bench_snapshot() -> Dict[str, Any]:
    p = _status_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_reasoning_quality_snapshot(payload: Dict[str, Any]) -> None:
    _quality_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_reasoning_quality_snapshot() -> Dict[str, Any]:
    p = _quality_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
