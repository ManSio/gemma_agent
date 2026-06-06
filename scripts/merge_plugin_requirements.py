#!/usr/bin/env python3
"""
Собрать pip-зависимости из module.json под корнями плагинов (по умолчанию modules + core_libraries).

  python scripts/merge_plugin_requirements.py --print
  python scripts/merge_plugin_requirements.py --install          # Docker / CI
  python scripts/merge_plugin_requirements.py --write requirements-plugins.generated.txt
  python scripts/merge_plugin_requirements.py --modules ./modules --install   # один корень

Переменные: MODULES_PATH, CORE_LIBRARIES_PATH, PLUGIN_MANIFEST_PATHS (через запятую).
Рантайм pip install запрещён политикой платформы — только сборка.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.plugin_requirements import (  # noqa: E402
    merge_plugin_requirements_report,
    write_merged_requirements_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge pip_requirements from plugin manifests")
    parser.add_argument("--print", action="store_true", help="Print merged requirements")
    parser.add_argument("--install", action="store_true", help="pip install merged list (build time only)")
    parser.add_argument(
        "--write",
        default="",
        metavar="FILE",
        help="Write merged requirements to FILE (e.g. requirements-plugins.generated.txt)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if two modules pin the same distribution differently",
    )
    parser.add_argument(
        "--modules",
        default="",
        help="If set, only scan this directory; otherwise all PLUGIN_MANIFEST_PATHS / default roots",
    )
    args = parser.parse_args()

    if args.modules:
        mods = Path(args.modules)
        if not mods.is_dir():
            mods = ROOT / args.modules
        roots: list[Path] | None = [mods]
    else:
        roots = None

    report = merge_plugin_requirements_report(roots)
    if args.strict and report.duplicate_distribution_keys:
        for c in report.duplicate_distribution_keys:
            print(f"CONFLICT {c}", file=sys.stderr)
        return 1
    if args.write:
        write_merged_requirements_file(Path(args.write), roots=roots, report=report)
    if args.print:
        print("\n".join(report.merged_lines))
    if args.install and report.merged_lines:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", *report.merged_lines],
            cwd=str(ROOT),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
