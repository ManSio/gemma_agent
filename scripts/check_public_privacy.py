#!/usr/bin/env python3
"""
Скан только git-tracked исходников на утечки PII (не venv, не бинарники).

  python scripts/check_public_privacy.py
  python scripts/check_public_privacy.py --ci   # GitHub Actions
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKLIST_LOCAL = ROOT / "config" / "privacy_blocklist.local.txt"
BLOCKLIST_EXAMPLE = ROOT / "config" / "privacy_blocklist.local.example.txt"

SKIP_DIR_PARTS = frozenset(
    {".git", "venv", ".venv", "node_modules", "__pycache__", "dist", "build", ".eggs"}
)
SKIP_FILE_SUFFIX = frozenset(
    {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".zip", ".tar", ".gz", ".so", ".dll", ".exe"}
)
# Только текстовые исходники — не bin/python и не wheel
SCAN_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".sh",
        ".yml",
        ".yaml",
        ".json",
        ".toml",
        ".mdc",
        ".example",
        ".ini",
        ".cfg",
        ".service",
    }
)
SCAN_BASENAMES = frozenset({".env.example", "Dockerfile", "Makefile"})

TELEGRAM_ID_ALLOW = frozenset(
    {
        "123456789",
        "900000001",
        "900000002",
        "900000099",
        "100999001",
        "1234567890",
        "7777777777",  # Telegram API examples in libs
        "4294967295",  # uint32 max в бинарниках/константах
    }
)

PUBLIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ssh_user_ipv4", re.compile(r"(?:deploy-host|root)@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")),
    ("lan_ipv4", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")),
    ("placeholder_ip_broken", re.compile(r"192\.168\.x\.x|x\.x\.x\.x")),
]

ALLOW_PATH_SUBSTR = (
    "OPS_PRIVATE.local.md",
    "TURN_RECONSTRUCTION_PRIVATE.local.md",
    "privacy_blocklist.local.txt",
    "privacy_history_replacements.local.txt",
    "privacy_blocklist.local.example",
    "privacy_history_replacements.local.example",
)


def _git_tracked_files() -> list[Path] | None:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return [ROOT / line.strip() for line in out.stdout.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _path_blocked(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in SKIP_DIR_PARTS for p in parts):
        return True
    if rel.startswith("data/") or rel == ".env":
        return True
    if "PRIVATE.local" in rel:
        return True
    if any(x in rel for x in ALLOW_PATH_SUBSTR):
        return True
    return False


def _is_scannable_source(path: Path) -> bool:
    if path.suffix.lower() in SKIP_FILE_SUFFIX:
        return False
    if path.name in SCAN_BASENAMES:
        return True
    return path.suffix.lower() in SCAN_SUFFIXES


def _iter_files() -> list[Path]:
    tracked = _git_tracked_files()
    if tracked is None:
        tracked = [p for p in ROOT.rglob("*") if p.is_file()]
    out: list[Path] = []
    for path in tracked:
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if _path_blocked(rel):
            continue
        if not _is_scannable_source(path):
            continue
        out.append(path)
    return out


def _load_private_blocklist() -> list[tuple[str, re.Pattern[str]]]:
    if not BLOCKLIST_LOCAL.is_file():
        return []
    out: list[tuple[str, re.Pattern[str]]] = []
    for i, raw in enumerate(BLOCKLIST_LOCAL.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("regex:"):
            out.append((f"local_L{i}", re.compile(line[6:].strip())))
        else:
            lit = line[7:].strip() if line.startswith("literal:") else line
            out.append((f"local_L{i}", re.compile(re.escape(lit), re.I)))
    return out


def _telegram_id_hits(rel: str, text: str) -> list[str]:
    if "tests/fixtures/telegram_test_ids.py" in rel:
        return []
    hits: list[str] = []
    for m in re.finditer(r"\b[1-9]\d{8,9}\b", text):
        val = m.group(0)
        if val in TELEGRAM_ID_ALLOW:
            continue
        line = text.count("\n", 0, m.start()) + 1
        hits.append(f"{rel}:{line}: [telegram_id] {val!r}")
    return hits


def scan(*, ci: bool = False) -> list[str]:
    patterns = list(PUBLIC_PATTERNS)
    if not ci:
        patterns.extend(_load_private_blocklist())
    hits: list[str] = []
    for path in _iter_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        hits.extend(_telegram_id_hits(rel, text))
        for name, rx in patterns:
            if name == "placeholder_ip_broken" and "example" in rel:
                continue
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{rel}:{line}: [{name}] {m.group(0)!r}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="Privacy leak scan (git sources only)")
    ap.add_argument("--ci", action="store_true", help="CI: без локального blocklist")
    args = ap.parse_args()
    ci = args.ci or os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    hits = scan(ci=ci)
    if hits:
        print("[FAIL] Возможные утечки в публичных файлах:")
        for h in hits[:40]:
            print(" ", h)
        if len(hits) > 40:
            print(f"  ... и ещё {len(hits) - 40}")
        return 1
    mode = "CI" if ci else "локально"
    print(f"[OK] check_public_privacy ({mode}): утечек не найдено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
