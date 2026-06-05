#!/usr/bin/env python3
"""Replace placeholder / stale GitHub URLs from config/repo_links.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "repo_links.json"

SKIP_DIR = {".git", "venv", ".venv", "node_modules", "__pycache__", "data", "dist", "build"}
SCAN_SUFFIX = {
    ".md",
    ".py",
    ".yml",
    ".yaml",
    ".example",
    ".fragment",
    ".txt",
    ".sh",
}


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR for part in path.parts):
            continue
        if path.suffix.lower() not in SCAN_SUFFIX and path.name not in {"Dockerfile", "Makefile"}:
            continue
        if "privacy_history_replacements.local" in path.name:
            continue
        out.append(path)
    return out


def _replacements(data: dict) -> list[tuple[str, str]]:
    org = str(data.get("github_org") or "").strip()
    repo = str(data.get("github_repo") or "gemma_agent").strip()
    branch = str(data.get("default_branch") or "master").strip()
    if not org or org == "ManSio":
        raise ValueError("Set github_org in config/repo_links.json first")

    base = f"https://github.com/{org}/{repo}"
    legacy_repo = "gemma-agent" if repo != "gemma-agent" else repo
    pairs: list[tuple[str, str]] = [
        ("ManSio", org),
        (f"https://github.com/ManSio/{legacy_repo}", base),
        (f"https://github.com/{org}/{legacy_repo}", base),
        ("https://github.com/ManSio/gemma_agent", base),
        ("/blob/master/", f"/blob/{branch}/"),
        ("/tree/master/", f"/tree/{branch}/"),
    ]
    # Longest first to avoid partial double-replace.
    return sorted(pairs, key=lambda x: len(x[0]), reverse=True)


def main() -> int:
    if not CONFIG.is_file():
        print(f"Missing {CONFIG}", file=sys.stderr)
        return 1
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        repl = _replacements(data)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    changed: list[str] = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        orig = text
        for old, new in repl:
            text = text.replace(old, new)
        if text != orig:
            path.write_text(text, encoding="utf-8")
            changed.append(path.relative_to(ROOT).as_posix())

    for rel in sorted(changed):
        print(f"updated {rel}")
    print(f"Done ({len(changed)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
