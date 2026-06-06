#!/usr/bin/env python3
"""
Применить фрагменты config/env_catalog/*.env.fragment к .env (локально или на сервере).

  python scripts/apply_env_catalog.py
  python scripts/apply_env_catalog.py /opt/gemma_agent/.env
  python scripts/apply_env_catalog.py --dry-run

Читает docs/OPS_PRIVATE.local.md для OWNER_TELEGRAM_ID / POST_DEPLOY / ADMIN (если есть).
Не перезаписывает TELEGRAM_TOKEN, OPENROUTER_* и прочие секреты — только ключи из каталога.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CATALOG = _ROOT / "config" / "env_catalog"
_OPS_PRIVATE = _ROOT / "docs" / "OPS_PRIVATE.local.md"

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _parse_ops_private() -> dict[str, str]:
    out: dict[str, str] = {}
    if not _OPS_PRIVATE.is_file():
        return out
    for line in _OPS_PRIVATE.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    owner = out.get("OWNER_TELEGRAM_ID") or out.get("PROBE_USER_ID") or ""
    probe = out.get("POST_DEPLOY_PROBE_USER_ID") or out.get("PROBE_USER_ID") or owner
    if owner:
        out.setdefault("OWNER_TELEGRAM_ID", owner)
        out.setdefault("ADMIN_USER_IDS", owner)
        out.setdefault("ADMIN_NOTIFY_USER_IDS", owner)
    if probe:
        out.setdefault("POST_DEPLOY_PROBE_USER_ID", probe)
    return out


def _fragment_files() -> list[Path]:
    if not _CATALOG.is_dir():
        return []
    gen = sorted((_CATALOG / "generated").glob("*.env.fragment")) if (_CATALOG / "generated").is_dir() else []
    hand = sorted(
        p for p in _CATALOG.glob("*.env.fragment") if p.parent == _CATALOG
    )
    return gen + hand  # ручные фрагменты перекрывают generated


def _parse_fragment(path: Path, ops: dict[str, str]) -> list[tuple[str, str, list[str]]]:
    """(key, value, comment_lines_before) — только строки KEY=value."""
    entries: list[tuple[str, str, list[str]]] = []
    comments: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if not line.strip():
            comments.append("")
            continue
        if line.strip().startswith("#"):
            comments.append(line)
            continue
        m = _KEY_RE.match(line.strip())
        if not m:
            comments.append(line)
            continue
        key, val = m.group(1), m.group(2)
        if key in ops and ops[key]:
            val = ops[key]
        entries.append((key, val, list(comments)))
        comments = []
    return entries


def _collect_catalog(ops: dict[str, str]) -> dict[str, tuple[str, list[str]]]:
    merged: dict[str, tuple[str, list[str]]] = {}
    for fp in _fragment_files():
        for key, val, cmt in _parse_fragment(fp, ops):
            merged[key] = (val, cmt)
    return merged


_PRESERVE_IF_NONEMPTY = frozenset(
    {
        "ADMIN_USER_IDS",
        "ADMIN_NOTIFY_USER_IDS",
        "TELEGRAM_TOKEN",
        "OPENROUTER_API_KEY",
        "OPENROUTER_API_KEY_DEV",
        "API_TOKEN",
        "QDRANT_API_KEY",
        "MEM0_API_KEY",
        "ENCRYPTION_KEY",
        "SECURITY_AES_KEY",
        "SECURITY_SALT",
        "BRAVE_SEARCH_API_KEY",
        "TAVILY_API_KEY",
        "LINK_REPUTATION_API_KEY",
    }
)


def _apply_to_env(env_path: Path, catalog: dict[str, tuple[str, list[str]]], *, dry_run: bool) -> list[str]:
    if not env_path.is_file():
        raise FileNotFoundError(env_path)
    lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    key_to_idx: dict[str, int] = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            key_to_idx[k] = i

    changed: list[str] = []
    append_block: list[str] = []

    for key, (val, cmt) in catalog.items():
        new_line = f"{key}={val}"
        if key in key_to_idx:
            idx = key_to_idx[key]
            if key in _PRESERVE_IF_NONEMPTY:
                old = lines[idx].split("=", 1)[-1].strip()
                if old and old not in ("123456789", ""):
                    continue
            if lines[idx].strip() != new_line:
                lines[idx] = new_line
                changed.append(f"update {key}")
        else:
            if cmt:
                append_block.extend(cmt)
            append_block.append(new_line)
            changed.append(f"add {key}")

    if append_block:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("")
        lines.extend(append_block)
        lines.append("")

    if changed and not dry_run:
        env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply env catalog fragments to .env")
    ap.add_argument("env_path", nargs="?", default=str(_ROOT / ".env"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    env_path = Path(args.env_path)
    ops = _parse_ops_private()
    catalog = _collect_catalog(ops)
    if not catalog:
        print("[ERR] no catalog entries", file=sys.stderr)
        return 1
    try:
        changed = _apply_to_env(env_path, catalog, dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    mode = "dry-run" if args.dry_run else "applied"
    if not changed:
        print(f"[OK] {env_path}: {mode}, nothing to change ({len(catalog)} keys checked)")
        return 0
    print(f"[OK] {env_path}: {mode}, {len(changed)} changes")
    for c in changed[:40]:
        print(f"  - {c}")
    if len(changed) > 40:
        print(f"  ... +{len(changed) - 40} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
