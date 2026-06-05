#!/usr/bin/env python3
"""Исследование: policy-dependent memory (EASMO) на ACC-сценариях gemma.

Запуск:
  python scripts/research_policy_memory.py
  python scripts/research_policy_memory.py --json-out data/benchmarks/policy_memory_latest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research.policy_memory import load_gemma_profiles, run_matrix  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Policy memory matrix (ACC offline)")
    ap.add_argument("--json-out", default="", help="сохранить полный отчёт JSON")
    args = ap.parse_args()

    report = run_matrix(profiles=load_gemma_profiles())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
