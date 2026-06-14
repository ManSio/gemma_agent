#!/usr/bin/env python3
"""One-off prod probes for persisted-state investigation."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/srv/gemma_bot")
_PROBE_UID = (os.environ.get("GEMMA_PROBE_USER_ID") or "").strip()


def _behavior_path() -> Path | None:
    """Путь к behavior JSON на VPS; uid только из env."""
    if not _PROBE_UID:
        return None
    return ROOT / "data/users/behavior" / f"{_PROBE_UID}__dm.json"


def main() -> int:
    beh_path = _behavior_path()
    if beh_path and beh_path.is_file():
        d = json.loads(beh_path.read_text(encoding="utf-8"))
        rp = d.get("routing_prefs") or {}
        print(f"=== BEHAVIOR {_PROBE_UID}__dm ===")
        print("pending_correction:", json.dumps(rp.get("pending_correction"), ensure_ascii=False)[:300])
        print("dialogue_slot:", rp.get("dialogue_slot"))
        print("recent_n:", len(d.get("recent_messages") or []))
        print("summary_len:", len(str(d.get("dialogue_summary") or "")))
        print("epoch:", d.get("conversation_epoch"))
        al = d.get("ephemeral_autolearn") or {}
        print("autolearn_buckets:", len((al.get("buckets") or {})))
        print("user_facts keys:", list((d.get("user_facts") or {}).keys())[:10])

    doc = json.loads((ROOT / "data/runtime/ephemeral_lessons.json").read_text(encoding="utf-8"))
    les = [x for x in doc.get("lessons", []) if x.get("active", True)]
    src = Counter((x.get("meta") or {}).get("source") or "none" for x in les)
    print("\n=== EPHEMERAL active", len(les), "sources", dict(src), "===")
    for trig in ("general", "news", "explain", "unknown"):
        hits = [x for x in les if x.get("trigger") == trig]
        if hits:
            print(f"--- trigger={trig} n={len(hits)} ---")
            print("instruction:", (hits[0].get("instruction") or "")[:240])

    cut = datetime.now(timezone.utc) - timedelta(days=14)
    rows = []
    for ln in (ROOT / "data/runtime/turns.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get("type") in ("scenario", "pre_send"):
            continue
        ts = str(r.get("ts") or "")
        if ts and ts < cut.isoformat():
            continue
        rows.append(r)

    def is_bad(r):
        return r.get("outcome") in ("clarify", "fallback") or "outcome_clarify" in (r.get("issues") or [])

    pre = [r for r in rows if str(r.get("ts", ""))[:10] < "2026-06-13"]
    post = [r for r in rows if str(r.get("ts", ""))[:10] >= "2026-06-13"]
    print("\n=== BAD RATE ===")
    print("pre_jun13:", len(pre), "bad", sum(is_bad(r) for r in pre))
    print("post_jun13:", len(post), "bad", sum(is_bad(r) for r in post))

    # Jun 13 weather cluster
    w = [
        r
        for r in rows
        if str(r.get("ts", "")).startswith("2026-06-13T11:")
        and "Погода" in str(r.get("assistant_excerpt") or "")
    ]
    print("\n=== JUN13 WEATHER CLUSTER", len(w), "===")
    for r in w[:6]:
        print(r.get("ts"), "|", str(r.get("user_excerpt") or "")[:50])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
