"""
Проверка структуры репозитория: каталог modules/* с module.json и ключевые файлы core.

Единая точка входа (вместо verify_structure_simple / verify_structure_final):

    python verify_structure.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CORE_REQUIRED = [
    "core/plugin_registry.py",
    "core/orchestrator.py",
    "core/input_layer.py",
    "core/brain/__init__.py",
    "core/input_handlers/register.py",
]


def discover_module_dirs(modules_root: Path) -> list[Path]:
    if not modules_root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(modules_root.iterdir()):
        if p.is_dir() and not p.name.startswith(".") and (p / "module.json").is_file():
            out.append(p)
    return out


def check_core_files() -> bool:
    ok = True
    print("=== Core files ===")
    for rel in CORE_REQUIRED:
        path = ROOT / rel
        if path.is_file():
            print(f"[OK] {rel}")
        else:
            print(f"[ERROR] missing {rel}")
            ok = False
    return ok


def check_modules() -> bool:
    print("\n=== modules/ (module.json + module.py) ===")
    modules_root = ROOT / "modules"
    if not modules_root.is_dir():
        print("[ERROR] directory modules/ not found")
        return False
    ok = True
    for mod_dir in discover_module_dirs(modules_root):
        rel = mod_dir.relative_to(ROOT)
        print(f"\n[OK] {rel}")
        for name in ("module.json", "module.py"):
            fp = mod_dir / name
            if fp.is_file():
                print(f"    [OK] {name}")
            else:
                print(f"    [ERROR] missing {name}")
                ok = False
        mj = mod_dir / "module.json"
        if mj.is_file():
            try:
                data = json.loads(mj.read_text(encoding="utf-8"))
                print(f"    name={data.get('name', '?')!r} type={data.get('type', '?')!r}")
            except Exception as e:
                print(f"    [ERROR] invalid JSON: {e}")
                ok = False
    count = len(discover_module_dirs(modules_root))
    print(f"\nTotal module packages with module.json: {count}")
    return ok


def main() -> int:
    print(f"Root: {ROOT}\n")
    a = check_core_files()
    b = check_modules()
    if a and b:
        print("\n[OK] All checks passed.")
        return 0
    print("\n[ERROR] Some checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
