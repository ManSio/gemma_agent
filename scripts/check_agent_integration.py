#!/usr/bin/env python3
"""Проверки интеграции агента: каталог команд ↔ хендлеры ↔ промпт (CI / перед релизом)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(ROOT / "tests" / "test_command_catalog.py"),
        str(ROOT / "tests" / "test_command_inventory.py"),
        str(ROOT / "tests" / "test_tools_prompt_coverage.py"),
        str(ROOT / "tests" / "test_command_catalog_tiers.py"),
        str(ROOT / "tests" / "test_goal_domain_policy.py"),
        str(ROOT / "tests" / "test_goal_runner_nudge.py"),
        str(ROOT / "tests" / "test_reasoning_loop_controller.py"),
        "-q",
        "--tb=short",
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
