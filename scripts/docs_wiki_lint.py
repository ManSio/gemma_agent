#!/usr/bin/env python3
"""Lint gemma_bot docs wiki (Karpathy-style health check). Read-only."""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
README = DOCS / "README.md"

STALE_PATTERNS = [
    (r"§9 live.*P0|§9 live 0/9|прогон.*§9 live|10 фраз.*gate", "§9 live как открытый gate (снято 31.05.2026)"),
    (r"TELEGRAM_PIPELINE_PRIVATE_PARALLEL=2(?!\s*—)", "parallel=2 как рекомендация"),
    (r"Probe 7/7\s*=\s*продукт|probe\s*=\s*продукт", "probe = продукт без оговорки"),
]

RETIRED_OK = [
    "REFORM_S9_RETIRED",
    "снят 31.05",
    "снято 31.05",
    "~~§9",
    "не просить прогон",
]


def _linked_from_readme(text: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"\]\(([^)#]+\.md)", text):
        out.add(m.group(1).replace("\\", "/"))
    return out


HISTORICAL_SKIP = ("причина:", "был ", "инцидент", "20.05", "historical", "~~§9")

EXPECTED_ORPHANS = frozenset({
    "OPS_PRIVATE.local.md",
    "TURN_RECONSTRUCTION_PRIVATE.local.md",
})


def _is_historical(line: str, lines: list[str], idx: int) -> bool:
    window = " ".join(lines[max(0, idx - 2) : idx + 1]).lower()
    return any(s in window for s in HISTORICAL_SKIP)


def main() -> int:
    if not README.is_file():
        print("[ERROR] docs/README.md missing")
        return 1

    readme = README.read_text(encoding="utf-8")
    linked = _linked_from_readme(readme)
    linked |= {"DOCS_MAINTENANCE_RU.md", "LLM_WIKI_GEMMA_RU.md", "PIPELINE_CALL_BRAIN_RU.md"}

    live = [
        p
        for p in DOCS.rglob("*.md")
        if "archive" not in p.parts and p.name != "README.md"
    ]
    orphans = []
    for p in sorted(live):
        rel = p.relative_to(DOCS).as_posix()
        if rel not in linked and p.name not in readme and rel not in readme:
            if p.name in EXPECTED_ORPHANS:
                continue
            orphans.append(rel)

    stale_hits: list[tuple[str, int, str, str]] = []
    for p in sorted(DOCS.rglob("*.md")):
        if "archive" in p.parts:
            continue
        if p.name in ("EXTERNAL_AI_SYNTHESIS_2026-05-30_RU.md",):
            continue  # снимок 30.05 с banner; таблица §9 — история
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            low = line.lower()
            for pat, label in STALE_PATTERNS:
                if not re.search(pat, line, re.I):
                    continue
                if "§9" in line and any(x in low for x in ("снят", "retired", "~~")):
                    continue
                if "parallel=2" in line.lower() and "не включ" in low:
                    continue
                ctx = " ".join(lines[max(0, i - 3) : i + 2])
                if any(ok in ctx for ok in RETIRED_OK):
                    continue
                if _is_historical(line, lines, i - 1):
                    continue
                rel = p.relative_to(ROOT).as_posix()
                stale_hits.append((rel, i, label, line.strip()[:120]))

    print(f"[docs_wiki_lint] {date.today().isoformat()}")
    print(f"  live docs (excl archive): {len(live)}")
    print(f"  not in README index: {len(orphans)}")
    for o in orphans:
        print(f"    [orphan] docs/{o}")
    print(f"  stale pattern hits: {len(stale_hits)}")
    for rel, ln, label, snippet in stale_hits:
        safe = snippet.encode("ascii", "replace").decode("ascii")
        print(f"    [stale] {rel}:{ln} — {label}")
        print(f"            {safe}")

    warn = len(orphans) + len(stale_hits)
    if warn:
        print(f"[WARN] docs_wiki_lint: {warn} item(s) — см. archive/DOCS_LINT_* или правь источник")
        return 0
    print("[OK] docs_wiki_lint")
    return 0


if __name__ == "__main__":
    sys.exit(main())
