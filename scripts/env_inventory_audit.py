#!/usr/bin/env python3
"""
Инвентаризация .env: дубликаты, осиротевшие ключи, пробелы example↔код.

  python scripts/env_inventory_audit.py
  python scripts/env_inventory_audit.py --env .env.example --fail-on-duplicates
  python scripts/env_inventory_audit.py --json

Сканирует Python (core/, modules/, scripts/, main.py, api.py) + bash (scripts/*.sh).
Учитывает os.getenv, os.environ, effective_bool/_env_int/_truthy и dynamic OPENROUTER_GEN_*.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_GETENV_RE = re.compile(
    r"""(?:os\.getenv|os\.environ\.get)\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""
)
_ENVINDEX_RE = re.compile(r"""os\.environ\[['"]([A-Z][A-Z0-9_]*)['"]\]""")
_SETDEFAULT_RE = re.compile(
    r"""os\.environ\.setdefault\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""
)
_WRAPPER_RE = re.compile(
    r"""(?:effective_bool|effective_int|effective_float|_env_int|_env_float|_env_bool|_env_str|_env_truthy|_truthy|_b|_i|env_truthy)\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""
)
_BASH_GREP_RE = re.compile(r"""^([A-Z][A-Z0-9_]*)=""")
_DYNAMIC_PREFIXES = (
    "OPENROUTER_GEN_",
    "BRAIN_OWN_TURN_ALLOW_",
)


def _parse_env_keys(path: Path) -> tuple[list[str], dict[str, int], list[str]]:
    """Active KEY= lines in order; duplicate map key→count; malformed lines."""
    if not path.is_file():
        return [], {}, [f"missing:{path}"]
    keys: list[str] = []
    counts: dict[str, int] = {}
    malformed: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            malformed.append(f"L{i}:{s[:80]}")
            continue
        m = _ASSIGN_RE.match(s)
        if not m:
            malformed.append(f"L{i}:{s[:80]}")
            continue
        k = m.group(1)
        keys.append(k)
        counts[k] = counts.get(k, 0) + 1
    return keys, counts, malformed


def _scan_python(root: Path) -> set[str]:
    found: set[str] = set()
    py_dirs = [root / "core", root / "modules", root / "scripts"]
    files = [root / "main.py", root / "api.py"]
    for d in py_dirs:
        if d.is_dir():
            files.extend(d.rglob("*.py"))
    for fp in files:
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat in (_GETENV_RE, _ENVINDEX_RE, _SETDEFAULT_RE, _WRAPPER_RE):
            found.update(pat.findall(text))
        if "OPENROUTER_GEN_" in text:
            found.add("OPENROUTER_GEN_*")
        if "BRAIN_OWN_TURN_ALLOW_" in text:
            found.add("BRAIN_OWN_TURN_ALLOW_*")
    return found


def _scan_bash(root: Path) -> set[str]:
    found: set[str] = set()
    sh_dir = root / "scripts"
    for fp in sh_dir.glob("*.sh"):
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            m = _BASH_GREP_RE.match(s)
            if m:
                found.add(m.group(1))
            # ${VAR:-default}
            for m2 in re.finditer(r"\$\{?([A-Z][A-Z0-9_]*)(?:[:?=-]|})", line):
                found.add(m2.group(1))
    return found


def _key_used(key: str, code_keys: set[str]) -> bool:
    if key in code_keys:
        return True
    for prefix in _DYNAMIC_PREFIXES:
        if key.startswith(prefix) and f"{prefix.rstrip('_')}*" in code_keys:
            return True
        if f"{prefix}*" in code_keys and key.startswith(prefix):
            return True
    return False


def audit(env_path: Path, root: Path) -> dict[str, Any]:
    keys, counts, malformed = _parse_env_keys(env_path)
    unique = sorted(set(keys))
    duplicates = sorted(k for k, c in counts.items() if c > 1)
    code_py = _scan_python(root)
    code_sh = _scan_bash(root)
    code_all = code_py | code_sh

    documented_only: list[str] = []
    for k in unique:
        if not _key_used(k, code_all):
            documented_only.append(k)

    # Keys in code but not in this env file
    example_set = set(unique)
    code_only: list[str] = []
    for k in sorted(code_all):
        if k.endswith("*"):
            continue
        if k not in example_set:
            code_only.append(k)

    return {
        "env_path": str(env_path),
        "active_lines": len(keys),
        "unique_keys": len(unique),
        "duplicate_keys": duplicates,
        "duplicate_count": len(duplicates),
        "malformed_lines": malformed,
        "code_keys_python": len(code_py),
        "code_keys_bash": len(code_sh),
        "used_in_code": len(unique) - len(documented_only),
        "documented_only": documented_only,
        "documented_only_count": len(documented_only),
        "code_only": code_only,
        "code_only_count": len(code_only),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit .env keys vs codebase")
    ap.add_argument("--env", default=str(_ROOT / ".env.example"))
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--fail-on-duplicates",
        action="store_true",
        help="Exit 1 if duplicate active KEY= lines",
    )
    ap.add_argument(
        "--fail-on-orphans",
        type=int,
        default=0,
        metavar="N",
        help="Exit 1 if documented_only count > N (0=ignore)",
    )
    args = ap.parse_args()
    rep = audit(Path(args.env), Path(args.root))

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"=== env inventory: {rep['env_path']} ===")
        print(
            f"  active={rep['active_lines']} unique={rep['unique_keys']} "
            f"used~{rep['used_in_code']} orphan={rep['documented_only_count']}"
        )
        if rep["duplicate_keys"]:
            print(f"  DUPLICATES ({rep['duplicate_count']}): {', '.join(rep['duplicate_keys'])}")
        if rep["malformed_lines"]:
            print(f"  MALFORMED ({len(rep['malformed_lines'])}):")
            for ln in rep["malformed_lines"][:10]:
                print(f"    {ln}")
        if rep["documented_only"]:
            print(f"  ORPHAN (no code ref, {rep['documented_only_count']}):")
            for k in rep["documented_only"][:30]:
                print(f"    {k}")
            if rep["documented_only_count"] > 30:
                print(f"    … +{rep['documented_only_count'] - 30} more")
        if rep["code_only"]:
            print(f"  CODE_ONLY (missing from env file, {rep['code_only_count']}):")
            for k in rep["code_only"][:25]:
                print(f"    {k}")
            if rep["code_only_count"] > 25:
                print(f"    … +{rep['code_only_count'] - 25} more")

    rc = 0
    if args.fail_on_duplicates and rep["duplicate_count"]:
        rc = 1
    if args.fail_on_orphans and rep["documented_only_count"] > args.fail_on_orphans:
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
