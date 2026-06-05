#!/usr/bin/env python3
"""
Синхронизация версий: корневой VERSION → README (RU/EN) и поле bundled_with во всех module.json.

Запуск из корня репозитория:
  python scripts/sync_versions.py              # всё
  python scripts/sync_versions.py --readme-only
  python scripts/sync_versions.py --plugins-only
  python scripts/sync_versions.py --date 2026-05-09   # ещё обновить дату релиза в README

Поле version в module.json — semver самого плагина; его скрипт не трогает
(после правок кода модуля — scripts/module_versions_git.py bump).
bundled_with — с какой версией бота манифест синхронизирован (трассировка релиза).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_app_version(root: Path) -> str:
    p = root / "VERSION"
    if not p.is_file():
        raise SystemExit(f"Нет файла {p}")
    v = p.read_text(encoding="utf-8").strip()
    if not v:
        raise SystemExit("VERSION пустой")
    return v


def sync_readme(path: Path, version: str, date: str | None) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    orig = text
    def _repl_ver(m: re.Match[str]) -> str:
        return m.group(1) + version + m.group(3)

    def _repl_date(m: re.Match[str]) -> str:
        assert date
        return m.group(1) + date + m.group(3)

    text = re.sub(
        r"(\*\*Version:\*\* `)([^`]+)(` \(see `VERSION`\))",
        _repl_ver,
        text,
        count=1,
    )
    text = re.sub(
        r"(\*\*Версия:\*\* `)([^`]+)(` \(см\. `VERSION`\))",
        _repl_ver,
        text,
        count=1,
    )
    if date:
        text = re.sub(
            r"(\*\*Release date:\*\* `)([^`]+)(`)",
            _repl_date,
            text,
            count=1,
        )
        text = re.sub(
            r"(\*\*Дата релиза:\*\* `)([^`]+)(`)",
            _repl_date,
            text,
            count=1,
        )
    if text != orig:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def iter_module_json(root: Path):
    for pat in ("modules/*/module.json", "core_libraries/*/module.json"):
        yield from sorted(root.glob(pat))


def stamp_bundled_with(path: Path, version: str) -> bool:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("bundled_with") == version:
        return False
    data["bundled_with"] = version
    out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(out, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Синхронизировать VERSION с README и module.json")
    ap.add_argument("--readme-only", action="store_true")
    ap.add_argument("--plugins-only", action="store_true")
    ap.add_argument("--date", default=None, help="Дата релиза YYYY-MM-DD для README (опционально)")
    args = ap.parse_args()
    root = repo_root()
    ver = read_app_version(root)
    do_readme = not args.plugins_only
    do_plugins = not args.readme_only
    changed = 0
    if do_readme:
        for rel in ("README.md", "README.ru.md"):
            if sync_readme(root / rel, ver, args.date):
                print(f"updated {rel}")
                changed += 1
    if do_plugins:
        for mj in iter_module_json(root):
            if stamp_bundled_with(mj, ver):
                print(f"updated {mj.relative_to(root)}")
                changed += 1
    if changed == 0:
        print(f"already in sync with VERSION={ver}")
    else:
        print(f"done; app version={ver}, files touched={changed}")


if __name__ == "__main__":
    main()
    sys.exit(0)
