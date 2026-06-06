#!/usr/bin/env python3
"""
Синхронизация PLUGIN_CONTROLLER_DENYLIST с config/modules_catalog.json.

Объединяет default_denylist (tier D+DEV) с уже заданными именами в .env
(например books_rag, heavy_module на проде) — ничего не удаляет.

  python scripts/sync_plugin_denylist_env.py --dry-run
  python scripts/sync_plugin_denylist_env.py --env-path /opt/gemma_agent/.env
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Set

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "config" / "modules_catalog.json"
KEY = "PLUGIN_CONTROLLER_DENYLIST"


def _load_catalog_deny() -> List[str]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return list(data.get("default_denylist") or [])


def _parse_env_deny(text: str) -> Set[str]:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(f"{KEY}="):
            raw = line.split("=", 1)[1].strip().strip('"').strip("'")
            return {x.strip().lower() for x in raw.split(",") if x.strip()}
    return set()


def merged_denylist(existing_env_text: str) -> List[str]:
    base = {x.strip().lower() for x in _load_catalog_deny() if x.strip()}
    base |= _parse_env_deny(existing_env_text)
    return sorted(base)


def _apply_to_env(
    path: Path,
    names: List[str],
    *,
    dry_run: bool,
    existing_text: str,
) -> None:
    text = existing_text
    new_line = f"{KEY}=" + ",".join(names)
    if re.search(rf"^{re.escape(KEY)}=", text, flags=re.MULTILINE):
        new_text = re.sub(
            rf"^{re.escape(KEY)}=.*$",
            new_line,
            text,
            count=1,
            flags=re.MULTILINE,
        )
    elif text.strip():
        new_text = text.rstrip() + "\n" + new_line + "\n"
    else:
        new_text = new_line + "\n"
    if dry_run:
        print(new_line)
        print(f"[dry-run] would write {len(names)} names to {path}")
        return
    path.write_text(new_text, encoding="utf-8")
    print(f"[OK] {path}: {len(names)} names in {KEY}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge catalog denylist into .env")
    ap.add_argument("--env-path", type=Path, default=ROOT / ".env", help="target .env file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not CATALOG.is_file():
        print(f"Нет {CATALOG}", file=sys.stderr)
        return 1
    env_path = args.env_path
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    names = merged_denylist(existing)
    _apply_to_env(env_path, names, dry_run=args.dry_run, existing_text=existing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
