"""Read-only сводка Memory Ops: JSONL память + хвост turns.jsonl (D2)."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


def _root() -> Path:
    return Path((os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or ".")


def _turns_path() -> Path:
    custom = (os.getenv("GEMMA_TURNS_LOG_PATH") or "").strip()
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else _root() / p
    return _root() / "data" / "runtime" / "turns.jsonl"


def _read_turns_tail(limit: int) -> List[Dict[str, Any]]:
    from core.turn_observer import read_recent_turns

    return read_recent_turns(limit=max(1, min(limit, 100)))


def _summarize_turns(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return ["turns.jsonl: (пусто или TURN_OBSERVER_ENABLED=false)"]
    lines = [f"turns.jsonl: последние {len(rows)} ходов (новые внизу списка ниже)", ""]
    profiles = Counter(str(r.get("profile") or "?") for r in rows)
    outcomes = Counter(str(r.get("outcome") or "?") for r in rows)
    lines.append(f"  profiles: {dict(profiles.most_common(6))}")
    lines.append(f"  outcomes: {dict(outcomes.most_common(4))}")
    gate_hits = sum(1 for r in rows if r.get("gate_verdict"))
    kv_sticky = sum(1 for r in rows if r.get("kv_profile_sticky"))
    if gate_hits:
        lines.append(f"  gate audit rows: {gate_hits}")
    if kv_sticky:
        lines.append(f"  kv_profile_sticky: {kv_sticky}")
    lines.append("")
    for r in rows[-8:]:
        ts = str(r.get("ts") or "")[:19]
        prof = str(r.get("profile") or "?")
        oc = str(r.get("outcome") or "?")
        topic = str(r.get("topic_current") or "")[:40]
        gate = str(r.get("gate_verdict") or "")
        rule = str(r.get("shortcut_rule_id") or "")
        ue = str(r.get("user_excerpt") or "")[:56]
        extra = ""
        if topic:
            extra += f" topic={topic!r}"
        if gate:
            extra += f" gate={gate}"
            if rule:
                extra += f"/{rule}"
        lines.append(f"  {ts} {oc} {prof}{extra} U:{ue!r}")
    return lines


def _misses_summary(tail: int) -> List[str]:
    path = _root() / "data" / "runtime" / "heuristic_misses.jsonl"
    if not path.is_file():
        return ["heuristic_misses.jsonl: (нет файла)"]
    try:
        lines_raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return [f"heuristic_misses.jsonl: {e}"]
    rows: List[Dict[str, Any]] = []
    for line in lines_raw[-max(1, tail) :]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return ["heuristic_misses.jsonl: (пусто)"]
    by_rule = Counter(str(r.get("rule_id") or "?") for r in rows)
    out = [f"heuristic_misses: {len(rows)} строк (tail={tail})", ""]
    for rid, n in by_rule.most_common(8):
        out.append(f"  {n:4d}  {rid}")
    return out


def build_memory_ops_report(
    *,
    user_id: str = "",
    turns_limit: int = 25,
    memory_limit: int = 5,
    misses_tail: int = 200,
    include_cli_hint: bool = False,
) -> str:
    uid = (user_id or "").strip() or None
    parts: List[str] = [
        "=== memory_ops_report (read-only) ===",
        "",
        "--- memory JSONL (кратко) ---",
    ]
    try:
        from core.memory_runtime_report import build_memory_insight_payload, format_memory_insight_plain

        payload = build_memory_insight_payload(user_id=uid, limit_per_file=memory_limit)
        plain = format_memory_insight_plain(payload)
        for ln in plain.splitlines()[:28]:
            parts.append(ln)
    except Exception as e:
        parts.append(f"(memory insight недоступен: {e})")
    parts.extend(["", "--- turns ---", ""])
    try:
        parts.extend(_summarize_turns(_read_turns_tail(turns_limit)))
    except Exception as e:
        parts.append(f"turns: ошибка {e}")
    parts.extend(["", "--- heuristic gate misses ---", ""])
    parts.extend(_misses_summary(misses_tail))
    parts.append("")
    parts.append(f"turns path: {_turns_path()}")
    if include_cli_hint:
        parts.append("hint: venv/bin/python3 scripts/memory_ops_report.py")
    return "\n".join(parts)


def shortcut_rule_id_from_turn_payload(payload: Dict[str, Any]) -> str:
    """Извлечь shortcut_rule_id из turn.outcome payload."""
    if not isinstance(payload, dict):
        return ""
    rid = str(payload.get("shortcut_rule_id") or "").strip()
    if rid:
        return rid
    ra = payload.get("router_route_audit")
    if not isinstance(ra, dict):
        return ""
    hg = ra.get("heuristic_gate")
    if not isinstance(hg, list) or not hg:
        return ""
    last = hg[-1] if isinstance(hg[-1], dict) else {}
    return str(last.get("shortcut_rule_id") or "").strip()
