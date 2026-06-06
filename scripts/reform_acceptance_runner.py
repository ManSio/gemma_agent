#!/usr/bin/env python3
"""
Route-only регрессия реформы (НЕ §9 Telegram).

  python scripts/reform_acceptance_runner.py
  python scripts/reform_acceptance_runner.py --json

Проверяет route_only из build_test_corpus._reform_acceptance_cases().
Полные сценарии: scripts/reform_chain_probe.py (orchestrator, без seed).
§9 в Telegram: docs/REFORM_S9_ACCEPTANCE_TRACKER_RU.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("BRAIN_OWN_TURN_ENABLED", "true")
    for k in ("NEWS", "WEATHER", "GEO_NEARBY", "AFFIRMATIVE_SEARCH"):
        os.environ.setdefault(f"BRAIN_OWN_TURN_ALLOW_{k}", "false")

    from core.agent_test_validators import validate_reply
    from scripts.build_test_corpus import _reform_acceptance_cases

    cases = _reform_acceptance_cases()
    rows = []
    failed = 0
    for case in cases:
        cid = str(case.get("id") or "?")
        text = str(case.get("text") or "")
        errs = validate_reply("", text, case)
        ok = not errs
        if not ok:
            failed += 1
        rows.append({"id": cid, "ok": ok, "errors": errs})

    doc = {
        "kind": "route_regression",
        "not_telegram_s9": True,
        "total": len(rows),
        "passed": len(rows) - failed,
        "failed": failed,
        "cases": rows,
    }
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = "OK" if r["ok"] else "FAIL"
            print(f"{mark}  {r['id']}")
            if r["errors"]:
                for e in r["errors"]:
                    print(f"      {e}")
        print(f"\n{doc['passed']}/{doc['total']} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
