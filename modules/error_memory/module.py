"""ErrorMemory: lightweight persistent memory of recurring error patterns."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from core.light_slash import parse_slash_args
from core.models import Output


def _path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "error_memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"patterns": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"patterns": []}
    except Exception:
        return {"patterns": []}


def _save(doc: Dict[str, Any]) -> None:
    _path().write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


class ErrorMemoryModule:
    async def execute(self, args: Dict[str, Any]):
        input_data = args.get("input") or {}
        payload = str(input_data.get("payload") or "").strip()
        cmd, rest = parse_slash_args(payload)
        if cmd != "error_memory":
            return Output(
                type="text",
                payload="/error_memory record:<pattern> | check:<text> | list",
                meta={"module": "error_memory"},
            )
        low = rest.strip()
        doc = _load()
        pats = [str(x).strip().lower() for x in (doc.get("patterns") or []) if str(x).strip()]
        if low.startswith("record:"):
            pat = low.split(":", 1)[1].strip().lower()
            if pat and pat not in pats:
                pats.append(pat)
                doc["patterns"] = pats[-200:]
                _save(doc)
            out = {"plugin": "error_memory", "status": "valid", "patterns": doc.get("patterns") or []}
            return Output(type="text", payload=json.dumps(out, ensure_ascii=False, indent=2), meta={"module": "error_memory"})
        if low.startswith("check:"):
            text = low.split(":", 1)[1].strip().lower()
            hits = [p for p in pats if p and p in text]
            out = {
                "plugin": "error_memory",
                "status": "invalid" if hits else "valid",
                "confidence": 0.86,
                "matched_patterns": hits,
                "patterns_total": len(pats),
            }
            return Output(type="text", payload=json.dumps(out, ensure_ascii=False, indent=2), meta={"module": "error_memory"})
        out = {"plugin": "error_memory", "status": "valid", "patterns": pats}
        return Output(type="text", payload=json.dumps(out, ensure_ascii=False, indent=2), meta={"module": "error_memory"})
