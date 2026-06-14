#!/usr/bin/env python3
"""Prod: how often experience_rules triggers actually hit user messages."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/srv/gemma_bot")
_PROBE_UID = (os.environ.get("GEMMA_PROBE_USER_ID") or "").strip()


def _behavior_path() -> Path:
    """Путь к behavior JSON на VPS; uid только из env (не в git)."""
    uid = _PROBE_UID
    if not uid:
        raise SystemExit("Set GEMMA_PROBE_USER_ID for behavior probe")
    return ROOT / "data/users/behavior" / f"{uid}__dm.json"


def main() -> int:
    doc = json.loads((ROOT / "data/runtime/ephemeral_lessons.json").read_text(encoding="utf-8"))
    les = [x for x in doc.get("lessons", []) if x.get("active", True)]
    exp_trigs = [str(x.get("trigger") or "") for x in les if (x.get("meta") or {}).get("source") == "experience_rules"]

    cut = datetime.now(timezone.utc) - timedelta(days=14)
    texts = []
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
        t = str(r.get("user_excerpt") or "")
        if t:
            texts.append(t.lower())

    hit_by_trig = Counter()
    turns_with_any_exp = 0
    for t in texts:
        hit = False
        for trig in exp_trigs:
            if trig and trig.lower() in t:
                hit_by_trig[trig] += 1
                hit = True
        if hit:
            turns_with_any_exp += 1

    print("=== experience_rules trigger hits in user_excerpt (14d) ===")
    print("turns_with_text:", len(texts))
    print("turns_matching_any_exp_rule:", turns_with_any_exp)
    print("top_triggers:", hit_by_trig.most_common(15))

    # pending_correction consumption estimate
    beh_path = _behavior_path()
    if beh_path.is_file():
        beh = json.loads(beh_path.read_text(encoding="utf-8"))
        pc = (beh.get("routing_prefs") or {}).get("pending_correction")
        print("\n=== pending_correction now ===")
        print(json.dumps(pc, ensure_ascii=False, indent=2))
    else:
        print("\n=== pending_correction: behavior file missing (set GEMMA_PROBE_USER_ID) ===")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
