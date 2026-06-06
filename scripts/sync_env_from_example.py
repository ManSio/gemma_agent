#!/usr/bin/env python3
"""
Синхронизировать .env с .env.example: структура + комментарии из example, значения из текущего .env.

  python scripts/sync_env_from_example.py
  python scripts/sync_env_from_example.py /opt/gemma_agent/.env
  python scripts/sync_env_from_example.py --dry-run

Секреты и непустые значения из целевого .env сохраняются. Плейсхолдеры example не затирают секреты.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_OPS = _ROOT / "docs" / "OPS_PRIVATE.local.md"

_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "123456789",
        "your.domain.com",
        "<значение>",
    }
)


def _parse_values(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _KEY_LINE.match(s)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _parse_ops_private() -> dict[str, str]:
    out: dict[str, str] = {}
    if not _OPS.is_file():
        return out
    for line in _OPS.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    owner = out.get("OWNER_TELEGRAM_ID") or out.get("PROBE_USER_ID") or ""
    if owner:
        out.setdefault("OWNER_TELEGRAM_ID", owner)
        out.setdefault("POST_DEPLOY_PROBE_USER_ID", out.get("POST_DEPLOY_PROBE_USER_ID") or owner)
    return out


def _merge_value(key: str, example_val: str, current: dict[str, str], ops: dict[str, str]) -> str:
    if key in ops and ops[key]:
        return ops[key]
    cur = current.get(key)
    ex = (example_val or "").strip()
    cu = (cur or "").strip()
    # Никогда не затирать уже заданное на сервере пустым плейсхолдером из example.
    if cu and (not ex or ex in _PLACEHOLDER_VALUES):
        return cu
    if cu and ex and cu not in _PLACEHOLDER_VALUES:
        return cu
    if ex and ex not in _PLACEHOLDER_VALUES:
        return ex
    return cu if cu else ex


def sync(example_path: Path, target_path: Path, *, dry_run: bool) -> int:
    if not example_path.is_file():
        raise FileNotFoundError(example_path)
    current = _parse_values(target_path) if target_path.is_file() else {}
    ops = _parse_ops_private()

    out_lines: list[str] = []
    replaced = 0
    for line in example_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            out_lines.append(line)
            continue
        m = _KEY_LINE.match(stripped)
        if not m:
            out_lines.append(line)
            continue
        key, ex_val = m.group(1), m.group(2)
        merged = _merge_value(key, ex_val, current, ops)
        if merged != ex_val:
            replaced += 1
        out_lines.append(f"{key}={merged}")

    if not dry_run:
        target_path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
    print(f"[OK] {target_path}: {'dry-run' if dry_run else 'written'}, values_merged={replaced}")
    return replaced


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default=str(_ROOT / ".env"))
    ap.add_argument("--example", default=str(_ROOT / ".env.example"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        sync(Path(args.example), Path(args.target), dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
