#!/usr/bin/env python3
"""
Линт слабых assert в tests/ — часть smoke release_guard.

Ловит паттерны из docs/TESTING_QUALITY_RU.md (Habr/OTUS anti-patterns).
Исключение: строка с `# quality: allow-weak-assert` и пояснением.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# Размытые union-assert (часто «и ok и clarify зелёные»).
_WEAK_UNION_RE = re.compile(
    r"assert\s+\w+\s+in\s*\(\s*[\"']ok[\"']\s*,\s*[\"']clarify[\"']",
    re.IGNORECASE,
)
_WEAK_UNION_ALT_RE = re.compile(
    r"assert\s+\w+\s+in\s*\(\s*[\"']clarify[\"']\s*,\s*[\"']ok[\"']",
    re.IGNORECASE,
)

# Единственный assert «not None» в теле функции — эвристика.
_DEF_RE = re.compile(r"^\s*def\s+(\w+)\s*\(", re.MULTILINE)
_ASSERT_NOT_NONE_RE = re.compile(r"^\s*assert\s+\w+\s+is\s+not\s+None\s*$", re.MULTILINE)
_ALLOW_TAG = "quality: allow-weak-assert"


def _iter_test_files(paths: Iterable[Path]) -> Iterable[Path]:
    for base in paths:
        if base.is_file() and base.suffix == ".py":
            yield base
        elif base.is_dir():
            yield from sorted(base.rglob("test_*.py"))


def _line_has_allow_tag(lines: List[str], line_idx: int) -> bool:
    window = lines[max(0, line_idx - 2) : line_idx + 2]
    return any(_ALLOW_TAG in ln for ln in window)


def _check_weak_unions(text: str, rel: str) -> List[str]:
    issues: List[str] = []
    lines = text.splitlines()
    for pat in (_WEAK_UNION_RE, _WEAK_UNION_ALT_RE):
        for m in pat.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            if _line_has_allow_tag(lines, line_no - 1):
                continue
            issues.append(f"{rel}:{line_no}: размытый union-assert (ok/clarify)")
    return issues


def _check_lone_not_none(text: str, rel: str) -> List[str]:
    issues: List[str] = []
    for m in _DEF_RE.finditer(text):
        start = m.start()
        # до следующего def на том же уровне — грубо до конца файла / следующего def с col 0
        rest = text[start:]
        next_def = _DEF_RE.search(rest, pos=1)
        body = rest[: next_def.start()] if next_def else rest
        if _ALLOW_TAG in body:
            continue
        asserts = list(_ASSERT_NOT_NONE_RE.finditer(body))
        if len(asserts) != 1:
            continue
        all_asserts = re.findall(r"^\s*assert\s+", body, re.MULTILINE)
        if len(all_asserts) != 1:
            continue
        fn = m.group(1)
        line_no = text[: asserts[0].start() + start].count("\n") + 1
        issues.append(
            f"{rel}:{line_no}: единственный assert «is not None» в {fn}() — усилить проверку"
        )
    return issues


def lint_paths(paths: List[Path]) -> Tuple[List[str], int]:
    all_issues: List[str] = []
    files = 0
    for p in _iter_test_files(paths):
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        text = p.read_text(encoding="utf-8")
        files += 1
        all_issues.extend(_check_weak_unions(text, rel))
        all_issues.extend(_check_lone_not_none(text, rel))
    return all_issues, files


def main() -> int:
    parser = argparse.ArgumentParser(description="Линт слабых assert в tests/")
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(TESTS)],
        help="Файлы или каталоги (по умолчанию tests/)",
    )
    args = parser.parse_args()
    roots = [ROOT / p for p in args.paths]
    issues, files = lint_paths(roots)
    if issues:
        print(f"[FAIL] test_quality_lint: {len(issues)} проблем в {files} файлах")
        for item in issues:
            print(f"  - {item}")
        print("См. docs/TESTING_QUALITY_RU.md §2, §4.1")
        return 1
    print(f"[OK] test_quality_lint: {files} файлов, слабых паттернов нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
