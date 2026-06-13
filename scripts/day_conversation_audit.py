#!/usr/bin/env python3
"""
Сводка за окно времени: где лежат «все переписки» vs агрегаты маршрута.

Полный текст диалога (личка / группа) — JSON behavior_store:
  $BEHAVIOR_DATA_DIR/behavior/<user_id>__<group_or_dm>.json
  по умолчанию на многих деплоях: data/users/behavior/*.json

Агрегат хода (intent, outcome, excerpts, KV-hints) — data/runtime/turns.jsonl
  пишется turn_observer только после bus turn.outcome; в orchestrator это
  ветка с plan.steps, user_id и непустым user_payload (см. core/orchestrator.py).

Полная трассировка для ops (вопрос+ответ+recent+план) — data/runtime/ops_trace.jsonl
  пишется record_ops_turn только если user_id, behavior_store и plan.steps (см. orchestrator).

Запуск на сервере:
  cd /opt/gemma_agent && python3 scripts/day_conversation_audit.py --hours 24
  python3 scripts/day_conversation_audit.py --hours 168 --root /opt/gemma_agent
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except ValueError:
        return None


def _behavior_dir(data: Path) -> Optional[Path]:
    for rel in ("users/behavior", "behavior"):
        p = data / rel
        if p.is_dir():
            return p
    return None


def _llm_usage_path(data: Path) -> Path:
    p1 = data / "runtime" / "llm_usage.jsonl"
    if p1.is_file():
        return p1
    return data / "llm_usage.jsonl"


def _scan_behavior(
    beh_dir: Path,
    cut: datetime,
) -> Tuple[int, List[Tuple[str, int, str, str, str, Optional[str]]]]:
    """Возвращает (число файлов с активностью в окне, строки для печати)."""
    rows_out: List[Tuple[str, int, str, str, str, Optional[str]]] = []
    active = 0
    for fp in sorted(beh_dir.glob("*.json")):
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
            d = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
        st = d.get("session_task") if isinstance(d.get("session_task"), dict) else {}
        ua = _parse_ts(st.get("updated_at"))
        rm = d.get("recent_messages")
        last_msg_ts: Optional[datetime] = None
        if isinstance(rm, list):
            for row in reversed(rm):
                if isinstance(row, dict):
                    last_msg_ts = _parse_ts(row.get("ts") or row.get("time"))
                    if last_msg_ts:
                        break
        in_window = False
        if ua and ua >= cut:
            in_window = True
        if last_msg_ts and last_msg_ts >= cut:
            in_window = True
        if mtime >= cut:
            in_window = True
        if not in_window:
            continue
        active += 1
        n = len(rm) if isinstance(rm, list) else 0
        li = str(st.get("last_intent") or "")
        lo = str(st.get("last_outcome") or "")
        lu = str(st.get("last_user_excerpt") or "")[:72]
        ts_ref = (
            ua.isoformat(timespec="seconds") if ua else (last_msg_ts.isoformat(timespec="seconds") if last_msg_ts else mtime.isoformat(timespec="seconds"))
        )
        rows_out.append((fp.name, n, li, lo, lu, ts_ref))
    return active, rows_out


def _scan_jsonl_window(
    path: Path,
    cut: datetime,
    ts_keys: Tuple[str, ...],
    *,
    hist_field: str = "",
) -> Tuple[int, Counter]:
    if not path.is_file():
        return 0, Counter()
    n = 0
    c: Counter = Counter()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, Counter()
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict):
            continue
        ts = None
        for k in ts_keys:
            ts = _parse_ts(o.get(k))
            if ts:
                break
        if not ts or ts < cut:
            continue
        n += 1
        if hist_field:
            key = str(o.get(hist_field) or "?")
            c[key] += 1
            continue
        if o.get("type") == "scenario":
            c["scenario"] += 1
            continue
        rs = o.get("reasoning")
        intent = str(o.get("intent") or "")
        if not intent and isinstance(rs, dict):
            intent = str(rs.get("intent") or "")
        c[intent or "?"] += 1
    return n, c


def main() -> int:
    ap = argparse.ArgumentParser(description="Day conversation / routing audit")
    ap.add_argument("--hours", type=float, default=24.0, help="Lookback window (UTC)")
    ap.add_argument("--root", default=str(ROOT), help="Project root (GEMMA_PROJECT_ROOT)")
    args = ap.parse_args()
    root = Path(args.root)
    data = root / "data"
    cut = datetime.now(timezone.utc) - timedelta(hours=max(0.5, float(args.hours)))

    print("=== day_conversation_audit ===")
    print(f"root: {root}")
    print(f"window_utc: >= {cut.isoformat(timespec='seconds')}")
    print()

    beh = _behavior_dir(data)
    if beh:
        act, rows = _scan_behavior(beh, cut)
        print(f"--- behavior_store ({beh}) ---")
        print(f"  json files with mtime or session_task or msg activity in window: {act}")
        for name, nmsg, li, lo, lu, tsr in rows[:40]:
            print(f"  • {name}")
            print(f"      recent_messages: {nmsg}  last_intent={li!r} last_outcome={lo!r}")
            print(f"      ref_ts={tsr}")
            if lu:
                print(f"      last_user_len={len(str(lu))}")
        if len(rows) > 40:
            print(f"  … +{len(rows) - 40} more files")
    else:
        print("--- behavior_store: directory not found (users/behavior or behavior) ---")

    tp = data / "runtime" / "turns.jsonl"
    n_turns, int_turns = _scan_jsonl_window(tp, cut, ("ts",))
    print()
    print(f"--- turns.jsonl ({tp.name}) ---")
    print(f"  lines in window: {n_turns}")
    if int_turns:
        top = int_turns.most_common(12)
        print(f"  intent top: {top}")

    op = data / "runtime" / "ops_trace.jsonl"
    n_ops, ch_ops = _scan_jsonl_window(op, cut, ("ts",), hist_field="channel")
    print()
    print(f"--- ops_trace.jsonl ---")
    print(f"  lines in window: {n_ops}")
    if ch_ops:
        print(f"  channel top: {ch_ops.most_common(12)}")

    lp = _llm_usage_path(data)
    n_llm, tag_llm = _scan_jsonl_window(lp, cut, ("ts", "timestamp"), hist_field="tag")
    print()
    print(f"--- llm_usage ({lp.relative_to(root) if lp.is_file() else lp}) ---")
    print(f"  lines in window: {n_llm}")
    if tag_llm:
        print(f"  tags (top): {tag_llm.most_common(8)}")

    print()
    print("--- hints ---")
    print("  Telegram: /admin_turns 80 — последние ходы из turns.jsonl")
    print("  Полный текст: cat behavior JSON (осторожно с PII) или бэкап через diagnostic_bundle")
    print("  Если turns/ops пусты за сутки, а behavior обновлялся — смотрите условия plan.steps / user_payload в orchestrator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
