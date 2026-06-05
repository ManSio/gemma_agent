#!/usr/bin/env python3
"""Список slash-команд из modules/*/module.json без запуска бота."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent / "modules"
    if not root.is_dir():
        print("modules/ not found", file=sys.stderr)
        return 1
    rows: list[tuple[str, str, str]] = []
    for mj in sorted(root.glob("*/module.json")):
        try:
            data = json.loads(mj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"{mj}: {e}", file=sys.stderr)
            continue
        name = str(data.get("name") or mj.parent.name)
        for c in data.get("commands") or []:
            if isinstance(c, str):
                t = c.strip()
                if not t.startswith("/"):
                    t = "/" + t.lstrip("/")
                rows.append((name, t, ""))
            elif isinstance(c, dict):
                t = str(c.get("trigger") or c.get("name") or "").strip()
                if not t:
                    continue
                if not t.startswith("/"):
                    t = "/" + t.lstrip("/")
                desc = str(c.get("description") or "").strip()
                rows.append((name, t, desc))
    for mod, trig, desc in rows:
        if desc:
            print(f"{mod}\t{trig}\t{desc}")
        else:
            print(f"{mod}\t{trig}")
    print(f"# total: {len(rows)} command entries", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
