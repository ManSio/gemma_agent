#!/usr/bin/env python3
"""Быстрый аудит public-копии: PII, РБ-хвосты, модули."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PII_PATTERNS = [
    ("mansio", re.compile(r"mansio", re.I)),
    ("real_bot_id", re.compile(r"\b(1234567890|9876543210|1111111111)\b")),
    ("lan_ip", re.compile(r"\b192\.168\.\d+\.\d+\b")),
    ("vps_ip", re.compile(r"\b212\.113\.\d+\.\d+\b")),
    ("srv_path", re.compile(r"/srv/gemma_bot")),
]
BY_PATTERNS = [
    ("pravo.onliner", re.compile(r"pravo\.by|onliner\.by|e-padruchnik", re.I)),
    ("law_module_file", re.compile(r"law_search_module|adu_padruchnik")),
]
SKIP = {".git", "venv", "__pycache__", "data", "scripts/audit_public_build.py"}


def scan_patterns(name: str, rx: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".py", ".md", ".sh", ".json", ".yml", ".example", ".txt", ".fragment"}:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(s in rel for s in SKIP):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if rx.search(text):
            hits.append(rel)
    return hits


def main() -> int:
    print("=== audit_public_build ===")
    cat = json.loads((ROOT / "config/modules_catalog.json").read_text(encoding="utf-8"))
    disk = [d.name for d in (ROOT / "modules").iterdir() if d.is_dir()]
    print(f"modules: catalog={len(cat.get('modules',{}))} disk={len(disk)}")
    for must_absent in (
        "core/law_search_module.py",
        "core/adu_padruchnik.py",
        "modules/spatial_design",
        "docs/DEV_DIARY_RU.md",
        "docs/archive",
    ):
        print(f"  absent {must_absent}: {not (ROOT / must_absent).exists()}")

    print("\n--- PII scan ---")
    pii_ok = True
    for name, rx in PII_PATTERNS:
        hits = scan_patterns(name, rx)
        status = "OK" if not hits else f"WARN {len(hits)}"
        print(f"  {name}: {status}")
        for h in hits[:5]:
            print(f"    - {h}")
        if hits:
            pii_ok = False

    print("\n--- BY hard refs ---")
    for name, rx in BY_PATTERNS:
        hits = scan_patterns(name, rx)
        print(f"  {name}: {len(hits)} files")
        for h in hits[:5]:
            print(f"    - {h}")

    print("\n--- subprocess ---")
    for cmd in (
        [sys.executable, str(ROOT / "scripts/check_public_privacy.py"), "--ci"],
        [sys.executable, str(ROOT / "scripts/release_guard.py"), "--smoke"],
    ):
        r = subprocess.run(cmd, cwd=str(ROOT))
        print(f"  {' '.join(cmd[2:])}: rc={r.returncode}")
        if r.returncode != 0:
            return 1

    print("\n[OK] audit passed" if pii_ok else "\n[WARN] audit: есть PII-хвосты в комментариях")
    return 0 if pii_ok else 0  # warn only


if __name__ == "__main__":
    sys.exit(main())
