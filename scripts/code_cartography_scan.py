#!/usr/bin/env python3
"""CLI: обновить ledger и историю, опционально вывести JSON сводки."""
from __future__ import annotations

import argparse
import json
import os
import sys

# Корень репозитория в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.code_cartography import (  # noqa: E402
    build_bundle_slice,
    compare_to_baseline,
    baseline_path,
    project_root,
    save_baseline,
    scan_and_maybe_record,
    scan_python_sources,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Карта кода gemma_bot (ledger + история)")
    p.add_argument("--root", help="CODE_CARTO_ROOT (каталог репозитория)")
    p.add_argument("--persist", action="store_true", help="Записать code_ledger.json и строку в history")
    p.add_argument("--baseline-save", action="store_true", help="Сохранить эталон code_baseline.json")
    p.add_argument("--baseline-diff", action="store_true", help="Печать JSON отличий от эталона")
    p.add_argument("--json", action="store_true", help="Печать build_bundle_slice в stdout")
    args = p.parse_args()
    if args.root:
        os.environ["CODE_CARTO_ROOT"] = args.root

    if args.baseline_save:
        files = scan_python_sources()
        out = save_baseline(files)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.baseline_diff:
        files = scan_python_sources()
        rep = compare_to_baseline(files, baseline_path())
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    if args.json:
        slice_ = build_bundle_slice(persist=args.persist)
        print(json.dumps(slice_, ensure_ascii=False, indent=2))
        return 0

    res = scan_and_maybe_record(persist=args.persist)
    print(f"root={project_root()} files={len(res.files)} ledger_written={res.ledger_written}")
    sl = res.snapshot.get("since_last_ledger") or {}
    print(
        f"delta vs ledger: +{len(sl.get('added') or [])} "
        f"-{len(sl.get('removed') or [])} "
        f"~{len(sl.get('modified') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
