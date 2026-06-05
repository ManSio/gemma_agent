#!/usr/bin/env python3
"""Убрать дубликаты KEY= в .env (оставить последнее значение — как при bash source)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ORPHAN_ADMIN = re.compile(r"^,\d+\s*$")


def dedupe_file(path: Path) -> int:
    if not path.is_file():
        print(f"missing {path}", file=sys.stderr)
        return 1
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    last_idx: dict[str, int] = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        m = _ASSIGN_RE.match(s)
        if m:
            last_idx[m.group(1)] = i

    out: list[str] = []
    removed = 0
    for i, line in enumerate(lines):
        bare = line.rstrip("\n\r")
        s = bare.strip()
        if _ORPHAN_ADMIN.match(s):
            removed += 1
            print(f"  drop orphan: {s[:60]}")
            continue
        if s and not s.startswith("#"):
            m = _ASSIGN_RE.match(s)
            if m and last_idx.get(m.group(1)) != i:
                removed += 1
                continue
            if not m and "=" not in s:
                removed += 1
                print(f"  drop orphan line: {s[:60]}")
                continue
        out.append(line)

    text = "".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    print(f"OK: {path} (removed {removed} duplicate/orphan line(s))")
    return 0


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    return dedupe_file(path)


if __name__ == "__main__":
    raise SystemExit(main())
