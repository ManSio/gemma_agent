"""
Daily highlights memory: short, vivid notes for the bot itself.

Stores one compact record per UTC day, so the assistant can "peek" recent
important moments without scanning heavy logs.
"""
from __future__ import annotations

import logging

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


logger = logging.getLogger(__name__)

def _runtime_dir() -> Path:
    return Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))


def highlights_path() -> Path:
    raw = (os.getenv("DAILY_HIGHLIGHTS_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _runtime_dir() / "daily_highlights.jsonl"


def _load_rows() -> List[Dict[str, Any]]:
    p = highlights_path()
    if not p.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = (line or "").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except OSError:
        return []
    return rows


def _save_rows(rows: List[Dict[str, Any]]) -> None:
    p = highlights_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = rows[-60:]  # up to ~2 months of daily notes
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in keep) + ("\n" if keep else "")
    tmp = str(p) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_day_highlights(*, slot: str, xray: Dict[str, Any], usage: Dict[str, Any], insights: List[str]) -> List[str]:
    notes: List[str] = []
    anomalies = xray.get("anomalies") if isinstance(xray.get("anomalies"), list) else []
    if anomalies:
        top = []
        for row in anomalies[:3]:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "?")
            detail = str(row.get("detail") or "").strip()
            top.append(f"{code}" + (f": {detail}" if detail else ""))
        if top:
            notes.append("Аномалии дня: " + " | ".join(top))

    top_queries = usage.get("top_queries") if isinstance(usage.get("top_queries"), list) else []
    qrows = []
    for row in top_queries[:3]:
        if not isinstance(row, dict):
            continue
        q = str(row.get("query") or "").strip()
        c = int(row.get("count") or 0)
        if q:
            qrows.append(f"{q}({c})")
    if qrows:
        notes.append("Яркие формулировки: " + ", ".join(qrows))

    top_int = usage.get("top_intents") if isinstance(usage.get("top_intents"), list) else []
    if top_int:
        try:
            i0 = top_int[0] if isinstance(top_int[0], dict) else {}
            notes.append(f"Доминировал intent={i0.get('intent')} ({int(i0.get('count') or 0)}).")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'daily_highlights', e, exc_info=True)
    for row in (insights or [])[:2]:
        if isinstance(row, str) and row.strip():
            notes.append(row.strip())

    if not notes:
        notes.append("Спокойный день: без ярко выраженных аномалий и всплесков.")
    return notes[:6]


def upsert_day_record(*, slot: str, payload: Dict[str, Any]) -> None:
    rows = _load_rows()
    out: List[Dict[str, Any]] = []
    replaced = False
    for row in rows:
        if str(row.get("slot") or "") == slot:
            out.append(payload)
            replaced = True
        else:
            out.append(row)
    if not replaced:
        out.append(payload)
    _save_rows(out)


def save_daily_highlights(*, slot: str, xray: Dict[str, Any], usage: Dict[str, Any], insights: List[str]) -> Dict[str, Any]:
    notes = build_day_highlights(slot=slot, xray=xray, usage=usage, insights=insights)
    payload = {
        "slot": slot,
        "saved_utc": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    upsert_day_record(slot=slot, payload=payload)
    return payload


def recent_highlights_hint(*, limit_days: int = 3, max_notes: int = 8) -> str:
    rows = _load_rows()
    if not rows:
        return ""
    picks = rows[-max(1, int(limit_days)) :]
    lines: List[str] = ["RecentDailyHighlights (кратко, внутренний контекст):"]
    n = 0
    for row in reversed(picks):
        slot = str(row.get("slot") or "?")
        notes = row.get("notes") if isinstance(row.get("notes"), list) else []
        for note in notes:
            if not isinstance(note, str) or not note.strip():
                continue
            lines.append(f"- [{slot}] {note.strip()}")
            n += 1
            if n >= max_notes:
                return "\n".join(lines)
    return "\n".join(lines) if n else ""
