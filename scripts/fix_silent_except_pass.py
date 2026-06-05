#!/usr/bin/env python3
"""Replace `except Exception: pass` with logger.debug (best-effort branches)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"^(\s+)except Exception:\n\1    pass\s*$", re.MULTILINE)


def _ensure_logger(text: str) -> str:
    if "logger = logging.getLogger" in text:
        return text
    if "import logging" not in text:
        m = re.search(r"^(from __future__ import annotations\n)", text, re.M)
        if m:
            text = text[: m.end()] + "\nimport logging\n" + text[m.end() :]
        else:
            text = "import logging\n" + text
    # После блока import/from, до первого def/class/@decorator
    lines = text.splitlines(keepends=True)
    insert_at = 0
    i = 0
    if lines and lines[0].startswith("from __future__"):
        i = 1
    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            i += 1
            continue
        if s.startswith("import ") or s.startswith("from "):
            i += 1
            continue
        insert_at = i
        break
    ins = "logger = logging.getLogger(__name__)\n\n"
    if insert_at < len(lines):
        lines.insert(insert_at, ins)
        text = "".join(lines)
    else:
        text = text + "\n" + ins
    return text


def fix_file(path: Path, *, dry_run: bool) -> int:
    text = path.read_text(encoding="utf-8")
    label = path.stem
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        ind = match.group(1)
        return (
            f"{ind}except Exception as e:\n"
            f"{ind}    logger.debug('%s optional failed: %s', {label!r}, e, exc_info=True)"
        )

    new = PATTERN.sub(repl, text)
    if count and not dry_run:
        if "logger" not in new or "import logging" not in new:
            new = _ensure_logger(new)
        path.write_text(new, encoding="utf-8")
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="files or dirs (default: core/input_layer.py)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    paths: list[Path] = []
    raw = args.paths or ["core/input_layer.py"]
    for raw_p in raw:
        p = Path(raw_p)
        if not p.is_absolute():
            p = ROOT / p
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.py")))
        else:
            paths.append(p)
    total = 0
    for p in paths:
        if p.name.startswith("test_"):
            continue
        n = fix_file(p, dry_run=args.dry_run)
        if n:
            print(f"{p.relative_to(ROOT)}: {n}")
            total += n
    print(f"total blocks: {total}" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
