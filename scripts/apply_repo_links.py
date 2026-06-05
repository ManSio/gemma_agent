#!/usr/bin/env python3
"""Replace REPLACE_ORG / placeholder URLs from config/repo_links.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "repo_links.json"

FILES = [
    "README.md",
    "README.ru.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/setup_help.yml",
    "docs/getting-started/quickstart.md",
    "docs/getting-started/quickstart.ru.md",
    "docs/PUBLISH_CHECKLIST.md",
]


def main() -> int:
    if not CONFIG.is_file():
        print(f"Missing {CONFIG}", file=sys.stderr)
        return 1
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    org = str(data.get("github_org") or "").strip()
    repo = str(data.get("github_repo") or "gemma-agent").strip()
    if not org or org == "REPLACE_ORG":
        print("Set github_org in config/repo_links.json first", file=sys.stderr)
        return 1
    base = f"https://github.com/{org}/{repo}"
    repl = {
        "REPLACE_ORG": org,
        "https://github.com/REPLACE_ORG/gemma-agent": base,
    }
    n = 0
    for rel in FILES:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        orig = text
        for old, new in repl.items():
            text = text.replace(old, new)
        if text != orig:
            path.write_text(text, encoding="utf-8")
            print(f"updated {rel}")
            n += 1
    print(f"Done ({n} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
