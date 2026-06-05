#!/usr/bin/env python3
"""
Офлайн дистилляция (Reflexio-style): experience_digest + 👎 + реальные stumble → черновики уроков.

Не пишет в ephemeral_lessons автоматически — только JSON для ревью / /remember_patch.

  python scripts/digest_lesson_drafts.py
  python scripts/digest_lesson_drafts.py --apply  # создать ephemeral через API ядра (осторожно)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _runtime() -> Path:
    return Path(os.getenv("GEMMA_PROJECT_ROOT") or ROOT) / "data" / "runtime"


def _read_jsonl(path: Path, limit: int = 5000) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _negative_feedback(path: Path) -> List[Dict[str, Any]]:
    p = path if path.is_file() else _runtime() / "user_feedback.jsonl"
    out = []
    for row in _read_jsonl(p):
        if str(row.get("rating") or row.get("score") or "") in ("-1", "-1.0"):
            out.append(row)
        elif row.get("negative") is True:
            out.append(row)
    return out


def _stumbles(path: Path) -> List[Dict[str, Any]]:
    from core.route_risk_memory import should_record_stumble

    p = path if path.is_file() else _runtime() / "route_risk.jsonl"
    out = []
    for row in _read_jsonl(p):
        oc = str(row.get("outcome") or "")
        if should_record_stumble(
            outcome=oc,
            detail=str(row.get("detail") or ""),
            user_feedback_negative=bool(row.get("user_feedback_negative")),
        ):
            out.append(row)
    return out


def cluster_drafts(
    *,
    digest_rows: List[Dict[str, Any]],
    feedback_rows: List[Dict[str, Any]],
    stumble_rows: List[Dict[str, Any]],
    min_cluster: int = 2,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def _key(row: Dict[str, Any]) -> str:
        fp = str(row.get("fp") or row.get("fingerprint") or "").strip()
        if fp:
            return fp
        mod = str(row.get("module") or row.get("planned_module") or "chat")
        intent = str(row.get("intent") or "general")
        ex = str(row.get("user_excerpt") or row.get("user_text") or "")[:80].lower()
        return f"{intent}|{mod}|{ex}"

    for src, rows in (
        ("experience", digest_rows),
        ("feedback", feedback_rows),
        ("stumble", stumble_rows),
    ):
        for row in rows:
            if str(row.get("outcome") or "").lower() in ("ok", "success"):
                continue
            k = _key(row)
            buckets[k].append({**row, "_draft_source": src})

    drafts: List[Dict[str, Any]] = []
    for key, items in buckets.items():
        if len(items) < min_cluster and items[0].get("_draft_source") != "feedback":
            if len(items) < 1:
                continue
        sample = items[-1]
        user_bit = str(
            sample.get("user_excerpt")
            or sample.get("user_text")
            or sample.get("text")
            or ""
        )[:120]
        detail = str(sample.get("detail") or sample.get("correction") or "")[:200]
        instruction = (
            f"Избегать повторения ошибки ({sample.get('_draft_source')}): "
            f"{detail or 'уточнять запрос, не уходить в неверный профиль.'}"
        )
        trigger = user_bit[:80] if len(user_bit) >= 12 else key.split("|")[-1][:80]
        drafts.append(
            {
                "cluster_key": key,
                "count": len(items),
                "sources": dict(Counter(str(i.get("_draft_source") or "?") for i in items)),
                "trigger": trigger,
                "match": "contains",
                "instruction": instruction[:500],
                "sample_intent": sample.get("intent"),
                "sample_module": sample.get("module"),
            }
        )
    drafts.sort(key=lambda d: (-int(d.get("count") or 0), str(d.get("trigger"))))
    return drafts


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft ephemeral lessons from runtime logs")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON path")
    parser.add_argument("--apply", action="store_true", help="Apply drafts via ephemeral_lessons.add_lesson")
    parser.add_argument("--min-cluster", type=int, default=2)
    args = parser.parse_args()
    rt = _runtime()
    digest = _read_jsonl(rt / "experience_digest.jsonl")
    feedback = _negative_feedback(rt / "user_feedback.jsonl")
    stumbles = _stumbles(rt / "route_risk.jsonl")
    drafts = cluster_drafts(
        digest_rows=digest,
        feedback_rows=feedback,
        stumble_rows=stumbles,
        min_cluster=max(1, args.min_cluster),
    )
    out_path = args.out or (rt / f"lesson_drafts_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "digest_rows": len(digest),
        "feedback_negative": len(feedback),
        "stumble_rows": len(stumbles),
        "drafts": drafts,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(drafts)} draft(s) -> {out_path}")
    if args.apply and drafts:
        from core.ephemeral_lessons import add_lesson

        applied = 0
        for d in drafts[:20]:
            trig = str(d.get("trigger") or "").strip()
            instr = str(d.get("instruction") or "").strip()
            if not trig or not instr:
                continue
            add_lesson(trigger=trig, instruction=instr, match="contains")
            applied += 1
        print(f"Applied {applied} lesson(s) via ephemeral_lessons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
