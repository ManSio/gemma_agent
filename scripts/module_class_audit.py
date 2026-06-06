#!/usr/bin/env python3
"""
Аудит 65 плагинов: сверка modules_catalog.json с диском, demo-маркеры, отчёт.

  python scripts/module_class_audit.py
  python scripts/module_class_audit.py --write-docs
  python scripts/module_class_audit.py --apply-manifest   # gemma_* в module.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "config" / "modules_catalog.json"
MODULES_DIR = ROOT / "modules"
DOCS_OUT = ROOT / "docs" / "MODULES_STATUS_RU.md"

_DEMO_RE = re.compile(
    r"(?i)(in a real implementation|for demo|simplified for demo|placeholder for|заглушк)",
)


def _load_catalog() -> Dict[str, Any]:
    if not CATALOG_PATH.is_file():
        raise SystemExit(f"Нет каталога: {CATALOG_PATH}")
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _scan_disk() -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for mj in sorted(MODULES_DIR.glob("*/module.json")):
        data = json.loads(mj.read_text(encoding="utf-8"))
        name = str(data.get("name") or "").strip()
        if name:
            out[name] = mj.parent
    return out


def _scan_demo_markers(folder: Path) -> List[str]:
    hits: List[str] = []
    py = folder / "module.py"
    if not py.is_file():
        return hits
    text = py.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if _DEMO_RE.search(line):
            hits.append(f"{py.name}:{i}")
    return hits


def audit(*, apply_manifest: bool = False, write_docs: bool = False) -> int:
    catalog = _load_catalog()
    cat_modules: Dict[str, Any] = catalog.get("modules") or {}
    disk = _scan_disk()
    errors: List[str] = []
    warnings: List[str] = []

    for name in sorted(set(cat_modules) - set(disk)):
        errors.append(f"catalog без папки: {name}")
    for name in sorted(set(disk) - set(cat_modules)):
        errors.append(f"папка без catalog: {name} ({disk[name].name})")

    rows: List[Tuple[str, str, str, str, str]] = []
    for name in sorted(cat_modules.keys()):
        if name not in disk:
            continue
        folder = disk[name]
        meta = cat_modules[name]
        tier = meta.get("tier", "?")
        demo = _scan_demo_markers(folder)
        has_tests = (folder / "tests.py").is_file()
        if demo:
            warnings.append(f"{name}: demo-маркер в {', '.join(demo)}")
        if tier in ("A", "C") and not has_tests and tier != "A":
            warnings.append(f"{name}: tier {tier} без tests.py")
        if apply_manifest:
            mj = folder / "module.json"
            data = json.loads(mj.read_text(encoding="utf-8"))
            data["description"] = meta.get("description_ru") or data.get("description") or ""
            data["gemma_tier"] = tier
            data["gemma_evidence"] = meta.get("evidence") or ""
            mj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows.append(
            (
                name,
                tier,
                meta.get("type", ""),
                "да" if has_tests else "—",
                "⚠ demo" if demo else "ok",
            )
        )

    if write_docs:
        _write_status_doc(catalog, rows, warnings)

    print(f"[audit] modules={len(rows)} denylist={len(catalog.get('default_denylist') or [])}")
    for w in warnings[:20]:
        print(f"[WARN] {w}")
    if len(warnings) > 20:
        print(f"[WARN] ... ещё {len(warnings) - 20}")
    for e in errors:
        print(f"[ERROR] {e}")

    if errors:
        return 1
    if warnings and not apply_manifest:
        print("[audit] есть предупреждения (demo/tests) — см. docs/MODULES_STATUS_RU.md")
    return 0


def _write_status_doc(catalog: Dict[str, Any], rows: List[Tuple[str, str, str, str, str]], warnings: List[str]) -> None:
    legend = catalog.get("tier_legend") or {}
    deny = catalog.get("default_denylist") or []
    lines = [
        "# Статус 65 модулей (автоген)",
        "",
        f"**Обновлено:** {catalog.get('updated', '')} · **Источник:** `config/modules_catalog.json`",
        "",
        "Пересборка:",
        "",
        "```bash",
        "python scripts/module_class_audit.py --write-docs",
        "python scripts/module_class_audit.py --apply-manifest",
        "```",
        "",
        "## Легенда tier",
        "",
        "| Tier | Смысл |",
        "|------|--------|",
    ]
    for k in ("A", "B", "C", "D", "DEV"):
        if k in legend:
            lines.append(f"| **{k}** | {legend[k]} |")
    lines.extend(
        [
            "",
            f"**PLUGIN_CONTROLLER_DENYLIST по умолчанию ({len(deny)}):**",
            "",
            "```env",
            "PLUGIN_CONTROLLER_DENYLIST=" + ",".join(deny),
            "```",
            "",
            "## Таблица",
            "",
            "| name | tier | type | tests.py | код |",
            "|------|------|------|----------|-----|",
        ]
    )
    for r in rows:
        lines.append(f"| `{r[0]}` | {r[1]} | {r[2]} | {r[3]} | {r[4]} |")
    if warnings:
        lines.extend(["", "## Предупреждения аудита", ""])
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    DOCS_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] wrote {DOCS_OUT.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Аудит modules_catalog vs 65 plugins")
    ap.add_argument("--write-docs", action="store_true", help="docs/MODULES_STATUS_RU.md")
    ap.add_argument("--apply-manifest", action="store_true", help="записать gemma_tier в module.json")
    args = ap.parse_args()
    if not args.write_docs and not args.apply_manifest:
        args.write_docs = True
    rc = audit(apply_manifest=args.apply_manifest, write_docs=args.write_docs)
    sys.exit(rc)


if __name__ == "__main__":
    main()
