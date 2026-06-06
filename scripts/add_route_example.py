#!/usr/bin/env python3
"""
Добавить route_only пример в data/learning/route_examples.jsonl.

  python scripts/add_route_example.py --profile news_brief --text "Какие новости"
  python scripts/add_route_example.py news_brief "погода в Минске"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.route_example_store import append_route_example, route_examples_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", nargs="?", help="Ожидаемый brain profile")
    ap.add_argument("text", nargs="?", help="Реплика пользователя")
    ap.add_argument("--profile", dest="profile_flag")
    ap.add_argument("--text", dest="text_flag")
    ap.add_argument("--by", default="cli")
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    prof = (args.profile_flag or args.profile or "").strip()
    body = (args.text_flag or args.text or "").strip()
    if not prof or not body:
        ap.error("need --profile and --text (or positional profile text)")
    try:
        rec = append_route_example(
            text=body,
            expected_profile=prof,
            added_by=args.by,
            tags=args.tag,
            note=args.note,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "path": str(route_examples_path()), "case": rec}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
