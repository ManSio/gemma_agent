#!/usr/bin/env python3
"""Print verifiable repo stats (tests, CI workflows, modules). For docs and CI summary."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _count_test_files() -> int:
    return len(list((ROOT / "tests").glob("test_*.py")))


def _pytest_collect_count() -> int:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
    )
    for line in reversed((r.stdout or "").splitlines()):
        if "tests collected" in line:
            return int(line.split()[0])
    return 0


def _anti_regression_count() -> int:
    sys.path.insert(0, str(ROOT))
    from scripts.release_guard import ANTI_REGRESSION_TESTS

    return len(ANTI_REGRESSION_TESTS)


def _workflow_files() -> list[str]:
    wf = ROOT / ".github" / "workflows"
    return sorted(p.name for p in wf.glob("*.yml")) if wf.is_dir() else []


def _module_count() -> int:
    import json

    cat = ROOT / "config" / "modules_catalog.json"
    if not cat.is_file():
        return 0
    data = json.loads(cat.read_text(encoding="utf-8"))
    modules = data.get("modules") or {}
    return len(modules)


def main() -> int:
    stats = {
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").is_file() else "?",
        "test_files": _count_test_files(),
        "pytest_collected": _pytest_collect_count(),
        "anti_regression_tests": _anti_regression_count(),
        "ci_workflows": _workflow_files(),
        "public_modules": _module_count(),
        "pytest_ini": (ROOT / "pytest.ini").is_file(),
        "dockerfile": (ROOT / "Dockerfile").is_file(),
        "docker_compose": (ROOT / "docker-compose.yml").is_file(),
    }
    print("Gemma Agent — repo stats (run locally to verify docs)")
    print("-" * 48)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("-" * 48)
    print("Verify: python -m pytest tests/ --collect-only -q")
    print("CI:     .github/workflows/ci.yml (on every push/PR)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
