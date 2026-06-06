#!/usr/bin/env python3
"""Сводка FAIL из chain-отчёта agent_test."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", default="data/testing/reports/full_audit_chain.jsonl")
    ap.add_argument("--md-out", default="")
    args = ap.parse_args()
    path = Path(args.report)
    if not path.is_absolute():
        path = _ROOT / path
    if not path.is_file():
        print(f"missing {path}", file=sys.stderr)
        return 2
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fails = [r for r in rows if not r.get("pass")]
    err_c = Counter()
    for r in fails:
        for e in r.get("errors") or []:
            err_c[e] += 1
    lines = [
        f"# Chain report: {path.name}",
        "",
        f"- total: {len(rows)}",
        f"- pass: {len(rows) - len(fails)}",
        f"- fail: {len(fails)}",
        "",
        "## Error codes",
        "",
    ]
    for code, n in err_c.most_common():
        lines.append(f"- `{code}`: {n}")
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    for r in fails[:40]:
        ch = r.get("chain") or {}
        ae = ch.get("after_execute") or {}
        lines.append(f"### {r.get('id')}")
        lines.append(f"- Q: {(r.get('user_text') or '')[:120]}")
        lines.append(f"- errors: {r.get('errors')}")
        lines.append(f"- profile: {ae.get('brain_profile') or ae.get('router_profile')}")
        lines.append(f"- module: {ae.get('planned_module')}")
        lines.append(f"- A: {(r.get('reply_preview') or '')[:160]}...")
        lines.append("")
    text = "\n".join(lines)
    print(text[:8000])
    if args.md_out:
        out = Path(args.md_out)
        if not out.is_absolute():
            out = _ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
