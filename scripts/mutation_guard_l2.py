#!/usr/bin/env python3
"""
Mutation testing для pure L2-модулей (mutmut).

CI: .github/workflows/mutation-l2.yml (weekly + manual).
Порог: MUTATION_L2_MIN_SCORE (default 60).

Канон: mutmut==2.4.5 — CLI --paths-to-mutate=MODULE + runner в setup.cfg overlay.
mutmut 3.x: не поддерживается в CI (FAIL).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent

L2_TARGETS: Dict[str, List[str]] = {
    "core/brain/profile_route_guard.py": ["tests/test_profile_route_guard.py"],
    "core/dialogue_recheck_anchor.py": ["tests/test_dialogue_recheck_anchor.py"],
    "core/timezone_inference.py": [
        "tests/test_wall_clock_intent.py",
        "tests/test_timezone_inference.py",
    ],
    "core/text_leak_scan.py": ["tests/test_text_leak_scan.py"],
}

QUICK_TARGETS = ["core/brain/profile_route_guard.py"]

_SETUP_CFG = ROOT / "setup.cfg"
_MUTANTS_DIR = ROOT / "mutants"
_MUTMUT_CACHE = ROOT / ".mutmut-cache"


def _mutmut_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _min_score() -> float:
    raw = os.getenv("MUTATION_L2_MIN_SCORE", "60")
    try:
        return float(raw)
    except ValueError:
        return 60.0


def _path_to_mutant_pattern(module: str) -> str:
    stem = module.replace("\\", "/").removesuffix(".py")
    return stem.replace("/", ".") + "*"


def _replace_mutmut_section(text: str, new_section: str) -> str:
    stripped = re.sub(r"\[mutmut\][\s\S]*?(?=\n\[|\Z)", "", text, count=1).rstrip()
    return stripped + "\n\n" + new_section.strip() + "\n"


@contextmanager
def _setup_cfg_overlay_v2(module: str, tests: List[str]):
    """mutmut 2.x: paths_to_mutate + runner в setup.cfg."""
    original = _SETUP_CFG.read_text(encoding="utf-8")
    runner = f"{sys.executable} -m pytest -x -q --rootdir={ROOT} " + " ".join(tests)
    block = f"""[mutmut]
paths_to_mutate=
    {module}
tests_dir=tests/
runner={runner}
"""
    _SETUP_CFG.write_text(_replace_mutmut_section(original, block), encoding="utf-8")
    try:
        yield
    finally:
        _SETUP_CFG.write_text(original, encoding="utf-8")


@contextmanager
def _setup_cfg_overlay_v3(module: str, tests: List[str]):
    """mutmut 3.x: only_mutate + pytest selection."""
    original = _SETUP_CFG.read_text(encoding="utf-8")
    test_block = "\n    ".join(tests)
    block = f"""[mutmut]
source_paths=
    core
only_mutate=
    {module}
pytest_add_cli_args_test_selection=
    {test_block}
pytest_add_cli_args=
    -x
    -q
"""
    _SETUP_CFG.write_text(_replace_mutmut_section(original, block), encoding="utf-8")
    try:
        yield
    finally:
        _SETUP_CFG.write_text(original, encoding="utf-8")


def _mutmut_major() -> Tuple[int, str]:
    try:
        import mutmut as mutmut_mod

        ver = getattr(mutmut_mod, "__version__", "") or ""
        m = re.search(r"(\d+)", ver)
        if m:
            return int(m.group(1)), ver.strip()
    except ImportError:
        pass
    return 0, ""


def _mutmut_version_ok() -> Tuple[bool, str]:
    """CI: только mutmut 2.4.x (setup.cfg paths_to_mutate). 3.x ломает guard."""
    _, ver = _mutmut_major()
    if ver.startswith("2.4."):
        return True, ver
    if ver:
        return False, ver
    return False, "unknown"


def _ci_fail_on_bad_mutmut() -> int | None:
    """В GitHub Actions не SKIP и не fallback — явный FAIL."""
    if not os.getenv("GITHUB_ACTIONS"):
        return None
    ok, ver = _mutmut_version_ok()
    if ok:
        return None
    print(
        f"[FAIL] mutmut==2.4.5 required, got {ver!r}. "
        "pip uninstall -y mutmut && pip install 'mutmut==2.4.5' --no-cache-dir --force-reinstall"
    )
    return 1


def _clear_mutants_cache() -> None:
    if _MUTMUT_CACHE.is_file():
        _MUTMUT_CACHE.unlink(missing_ok=True)
    if _MUTANTS_DIR.is_dir():
        shutil.rmtree(_MUTANTS_DIR, ignore_errors=True)


def _parse_score(text: str) -> float:
    body = text or ""
    # mutmut 2.4 progress (stderr): "273/273  🎉 76  ⏰ 0  🤔 0  🙁 197  🔇 0"
    for ln in reversed(body.splitlines()):
        em = re.search(
            r"(\d+)\s*/\s*(\d+)\s+.*?\U0001f389\s*(\d+).*?[\U0001f641\U0001f615]\s*(\d+)",
            ln,
        )
        if em:
            total_all = int(em.group(2))
            killed = int(em.group(3))
            survived = int(em.group(4))
            total = max(total_all, killed + survived)
            if total > 0:
                return 100.0 * killed / total
    m = re.search(r"(\d+)\s+/\s*(\d+)\s+.*survived", body, re.IGNORECASE)
    if m:
        survived = int(m.group(1))
        total = int(m.group(2))
        if total <= 0:
            return 0.0
        return 100.0 * (total - survived) / total
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*%", body)
    if m2:
        return float(m2.group(1))
    killed_m = re.search(r"(?:killed|\U0001f389)[^\d]*\(?\s*(\d+)", body, re.IGNORECASE)
    survived_m = re.search(
        r"(?:survived|[\U0001f641\U0001f615])[^\d]*\(?\s*(\d+)",
        body,
        re.IGNORECASE,
    )
    if killed_m or survived_m:
        killed = int(killed_m.group(1)) if killed_m else 0
        survived = int(survived_m.group(1)) if survived_m else 0
        total = killed + survived
        if total > 0:
            return 100.0 * killed / total
    killed = len(re.findall(r"\bkilled\b", body, re.IGNORECASE))
    survived = len(re.findall(r"\bsurvived\b", body, re.IGNORECASE))
    total = killed + survived
    if total > 0:
        return 100.0 * killed / total
    return 0.0


def _mutmut_results() -> Tuple[float, str]:
    results = subprocess.run(
        [sys.executable, "-m", "mutmut", "results"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=_mutmut_env(),
    )
    results_out = (results.stdout or "") + (results.stderr or "")
    return _parse_score(results_out), results_out


def _run_mutmut_setupcfg_v2(module: str, tests: List[str]) -> Tuple[float, str]:
    """mutmut 2.4.x: обязателен CLI --paths-to-mutate (setup.cfg overlay часто не подхватывается в CI)."""
    with _setup_cfg_overlay_v2(module, tests):
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "mutmut",
                "run",
                f"--paths-to-mutate={module}",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=_mutmut_env(),
        )
    out = (r.stdout or "") + (r.stderr or "")
    score_run = _parse_score(out)
    score, results_out = _mutmut_results()
    combined = max(score, score_run)
    if r.returncode != 0 and combined <= 0:
        return combined, out + "\n" + results_out
    return combined, (results_out or out)


def _run_mutmut_v3(module: str, tests: List[str]) -> Tuple[float, str]:
    pattern = _path_to_mutant_pattern(module)
    with _setup_cfg_overlay_v3(module, tests):
        r = subprocess.run(
            [sys.executable, "-m", "mutmut", "run", pattern],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
    out = (r.stdout or "") + (r.stderr or "")
    score, results_out = _mutmut_results()
    if r.returncode != 0 and score <= 0:
        return score, out + "\n" + results_out
    return score, results_out or out


def _run_mutmut(module: str, tests: List[str], *, show_only: bool) -> Tuple[float, str]:
    existing = [t for t in tests if (ROOT / t).is_file()]
    if not existing:
        return 0.0, f"[SKIP] нет тестов для {module}"

    major, ver = _mutmut_major()
    _clear_mutants_cache()

    if major >= 3:
        return (
            -1.0,
            "[FAIL] mutmut 3.x — нужен 2.4.5 (pip install 'mutmut==2.4.5' --force-reinstall)",
        )
    score, report = _run_mutmut_setupcfg_v2(module, existing)
    if score <= 0 and "No such option" in report and "--paths-to-mutate" in report:
        return (
            -1.0,
            report
            + "\n[FAIL] mutmut 3.x без --paths-to-mutate — pip install 'mutmut==2.4.5' --force-reinstall",
        )

    if show_only:
        return score, report
    return score, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Mutation guard для L2 pure modules")
    parser.add_argument("--quick", action="store_true", help="Только profile_route_guard")
    parser.add_argument("--show", action="store_true", help="Показать results, не падать по порогу")
    args = parser.parse_args()

    bad = _ci_fail_on_bad_mutmut()
    if bad is not None:
        return bad

    major, ver_text = _mutmut_major()
    if major == 0:
        print("[SKIP] mutmut не установлен — pip install -r requirements-dev.txt")
        return 0
    if sys.platform == "win32":
        print("[SKIP] mutmut run только на Linux CI или WSL")
        return 0

    targets = QUICK_TARGETS if args.quick else list(L2_TARGETS.keys())
    threshold = _min_score()
    print(f"Root: {ROOT}")
    print(f"mutmut: {ver_text or major} (major={major})")
    print(f"Порог MUTATION_L2_MIN_SCORE: {threshold:.0f}%")

    ok = True
    for module in targets:
        tests = L2_TARGETS.get(module, [])
        print(f"\n=== {module} ===")
        score, report = _run_mutmut(module, tests, show_only=args.show)
        print(report.strip() or "(нет вывода mutmut)")
        print(f"Оценка mutation: {score:.1f}%")
        if score < 0:
            print(f"[FAIL] {module}: несовместимая версия mutmut или устаревший guard")
            ok = False
        elif not args.show and score < threshold:
            print(f"[FAIL] {module}: {score:.1f}% < {threshold:.0f}%")
            ok = False
        elif not args.show:
            print(f"[OK] {module}")

    if args.show:
        return 0
    if not ok:
        print("\n[FAIL] mutation_guard_l2 — см. docs/TESTING_QUALITY_RU.md §4.2")
        return 1
    print("\n[OK] mutation_guard_l2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
