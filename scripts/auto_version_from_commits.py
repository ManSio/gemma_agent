#!/usr/bin/env python3
"""
Авто-предложение semver по conventional commits за диапазон git REF..HEAD.

Плагины (modules/*, core_libraries/*): для каждого модуля смотрит коммиты, затронувшие папку;
  игнорирует правки только bundled_with в module.json (как sync_versions).
Проект (корневой VERSION): коммиты, затронувшие core/, main.py, api.py, requirements.txt, pyproject.toml.

Правила сообщений: см. core/release_conventional.py (feat→minor, fix→patch, BREAKING/!→major).
Если в коммите нет распознанного типа, но изменения есть — по умолчанию patch (--fallback none чтобы не трогать).

Запуск из корня репозитория:
  python scripts/auto_version_from_commits.py                    # dry-run; auto: тег → иначе HEAD~1 → иначе корень репо
  python scripts/auto_version_from_commits.py --since root       # от первого коммита ветки до HEAD
  python scripts/auto_version_from_commits.py --since HEAD~5
  python scripts/auto_version_from_commits.py --apply
  python scripts/auto_version_from_commits.py --apply --sync-readme --date 2026-05-09
  python scripts/auto_version_from_commits.py --plugins-only
  python scripts/auto_version_from_commits.py --app-only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.release_conventional import (
    BumpKind,
    bump_semver,
    is_app_path,
    max_bump,
    parse_conventional_bump,
    strip_bundled,
)


def repo_root() -> Path:
    return _REPO_ROOT


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
    )


def git_toplevel(root: Path) -> Optional[Path]:
    p = run_git(root, "rev-parse", "--show-toplevel", check=False)
    if p.returncode != 0:
        return None
    return Path(p.stdout.strip())


def _git_root_commit(root: Path) -> Optional[str]:
    r = run_git(root, "rev-list", "--max-parents=0", "HEAD", check=False)
    if r.returncode != 0:
        return None
    line = ((r.stdout or "").splitlines() or [""])[0].strip()
    return line or None


def resolve_since(root: Path, since: Optional[str]) -> str:
    raw = (since or "").strip()
    if raw and raw.lower() not in ("auto", ""):
        if raw.lower() in ("root", "initial"):
            h = _git_root_commit(root)
            if not h:
                raise SystemExit("Не удалось получить корневой коммит (git rev-list --max-parents=0 HEAD).")
            return h
        return raw
    p = run_git(root, "describe", "--tags", "--abbrev=0", check=False)
    if p.returncode == 0 and (p.stdout or "").strip():
        tag = (p.stdout or "").strip()
        print(f"auto: последний тег — {tag}", file=sys.stderr)
        return tag
    prev = run_git(root, "rev-parse", "HEAD~1", check=False)
    if prev.returncode == 0 and (prev.stdout or "").strip():
        ref = prev.stdout.strip()
        print(
            "auto: тегов нет — беру HEAD~1..HEAD (предыдущий коммит). "
            "Для всей истории ветки: --since root; для своей точки: --since <хеш/тег>.",
            file=sys.stderr,
        )
        return ref
    h = _git_root_commit(root)
    if h:
        print(
            "auto: тегов нет и это один коммит на ветке — беру корень репозитория "
            "(диапазон root..HEAD может быть пуст). Создайте теги для удобных релизов.",
            file=sys.stderr,
        )
        return h
    raise SystemExit("Не удалось определить начало диапазона: задайте явно --since REF.")


def iter_commits_with_files(root: Path, since: str) -> List[Tuple[str, str, str, List[str]]]:
    r = run_git(root, "rev-list", "--reverse", f"{since}..HEAD", check=False)
    if r.returncode != 0:
        return []
    hashes = [h.strip() for h in r.stdout.splitlines() if h.strip()]
    out: List[Tuple[str, str, str, List[str]]] = []
    for h in hashes:
        m = run_git(root, "log", "-1", "--format=%s\x1f%b", h, check=False)
        if m.returncode != 0:
            continue
        subj, _, body = m.stdout.partition("\x1f")
        f = run_git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", h, check=False)
        files = [x.strip().replace("\\", "/") for x in f.stdout.splitlines() if x.strip()]
        out.append((h, subj.strip(), body.strip(), files))
    return out


def git_show_json(root: Path, rev: str, rel: str) -> Optional[Dict[str, Any]]:
    p = run_git(root, "show", f"{rev}:{rel}", check=False)
    if p.returncode != 0 or not (p.stdout or "").strip():
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def commit_counts_for_module_semver(root: Path, h: str, mod_root: str, files: List[str]) -> bool:
    """Учитывать коммит для semver модуля (не только bundled_with в module.json)."""
    prefix = mod_root + "/"
    touched = [f.replace("\\", "/") for f in files if f.replace("\\", "/").startswith(prefix)]
    if not touched:
        return False
    mj = f"{mod_root}/module.json"
    non_mj = [f for f in touched if f != mj]
    if non_mj:
        return True
    new = git_show_json(root, h, mj)
    if new is None:
        return True
    p = run_git(root, "rev-parse", f"{h}^", check=False)
    if p.returncode != 0:
        return True
    old = git_show_json(root, p.stdout.strip(), mj)
    if old is None:
        return True
    return strip_bundled(old) != strip_bundled(new)


def iter_module_dirs(root: Path) -> List[str]:
    out: List[str] = []
    for pat in ("modules/*/module.json", "core_libraries/*/module.json"):
        for mj in sorted(root.glob(pat)):
            out.append(mj.parent.relative_to(root).as_posix())
    return out


def plan_module_bump(
    root: Path,
    commits: List[Tuple[str, str, str, List[str]]],
    mod_root: str,
    fallback: Optional[BumpKind],
) -> Optional[BumpKind]:
    kinds: List[Optional[BumpKind]] = []
    any_semver = False
    for h, subj, body, files in commits:
        if not commit_counts_for_module_semver(root, h, mod_root, files):
            continue
        any_semver = True
        kinds.append(parse_conventional_bump(subj, body))
    level = max_bump(kinds)
    if level is not None:
        return level
    if any_semver and fallback:
        return fallback
    return None


def plan_app_bump(
    commits: List[Tuple[str, str, str, List[str]]],
    fallback: Optional[BumpKind],
) -> Optional[BumpKind]:
    kinds: List[Optional[BumpKind]] = []
    any_touch = False
    for _h, subj, body, files in commits:
        if not any(is_app_path(f) for f in files):
            continue
        any_touch = True
        kinds.append(parse_conventional_bump(subj, body))
    level = max_bump(kinds)
    if level is not None:
        return level
    if any_touch and fallback:
        return fallback
    return None


def read_version_file(root: Path) -> str:
    p = root / "VERSION"
    if not p.is_file():
        raise SystemExit(f"Нет {p}")
    v = p.read_text(encoding="utf-8").strip()
    if not v:
        raise SystemExit("VERSION пустой")
    return v


def append_module_history(root: Path, module_rel: str, was: str, new: str, ref: str, note: str) -> None:
    path = root / "docs/MODULE_VERSION_HISTORY.md"
    header = """# История версий модулей

Колонка **Было** — semver в `module.json` до bump; **Стало** — после.

| UTC дата | Модуль | Было | Стало | commit | примечание |
|----------|--------|------|-------|--------|------------|
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(header, encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        if "UTC дата" not in text:
            path.write_text(header, encoding="utf-8")
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    safe = lambda s: str(s).replace("|", "\\|")
    row = f"| {when} | `{safe(module_rel)}` | `{safe(was)}` | `{safe(new)}` | `{safe(ref)}` | {safe(note)} |\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(row)


def main() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Авто-semver по conventional commits (REF..HEAD)")
    ap.add_argument(
        "--since",
        default="auto",
        metavar="REF",
        help="Начало диапазона: хеш, тег, HEAD~N, root (корень репо), auto=тег|HEAD~1|корень",
    )
    ap.add_argument("--apply", action="store_true", help="Записать VERSION и module.json")
    ap.add_argument("--sync-readme", action="store_true", help="После apply вызвать sync_versions.py")
    ap.add_argument("--date", default=None, help="Дата для sync_versions (YYYY-MM-DD)")
    ap.add_argument("--plugins-only", action="store_true")
    ap.add_argument("--app-only", action="store_true")
    ap.add_argument(
        "--fallback",
        choices=("patch", "none"),
        default="patch",
        help="Если conventional-тип не распознан, но файлы менялись: patch | none",
    )
    args = ap.parse_args()

    root = repo_root()
    top = git_toplevel(root)
    if top is None or top.resolve() != root.resolve():
        raise SystemExit("Запуск из корня git-репозитория проекта.")

    since = resolve_since(root, args.since)
    commits = iter_commits_with_files(root, since)
    if not commits:
        print(f"Нет коммитов в {since!r}..HEAD — нечего оценивать.")
        return

    since_hint = since if len(since) <= 14 else f"{since[:7]}…"
    print(f"Диапазон {since_hint!r}..HEAD: коммитов в оценке — {len(commits)}")

    fb: Optional[BumpKind] = "patch" if args.fallback == "patch" else None
    ref_short = run_git(root, "rev-parse", "--short", "HEAD", check=False).stdout.strip() or "?"

    do_plugins = not args.app_only
    do_app = not args.plugins_only
    plugin_bump_count = 0
    wrote_any_version = False

    if do_plugins:
        print("=== Плагины / core_libraries (module.json version) ===")
        for mod in iter_module_dirs(root):
            kind = plan_module_bump(root, commits, mod, fb)
            if kind is None:
                continue
            mj = root / mod / "module.json"
            data = json.loads(mj.read_text(encoding="utf-8"))
            old_v = str(data.get("version", "0.0.0"))
            try:
                new_v = bump_semver(old_v, kind)
            except ValueError as e:
                print(f"skip {mod}: {e}")
                continue
            plugin_bump_count += 1
            print(f"  {mod}: {old_v} -> {new_v} ({kind})")
            if args.apply:
                data["version"] = new_v
                mj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                append_module_history(root, mod, old_v, new_v, ref_short, f"auto conventional ({since}..HEAD)")
                wrote_any_version = True
        if plugin_bump_count == 0:
            print(
                "  (плагины: нет коммитов, которые меняют код/манифест модуля смыслово — "
                "часто это только поле bundled_with после sync_versions или правки вне modules/*/ )"
            )

    app_kind: Optional[BumpKind] = None
    if do_app:
        print("=== Проект (корневой VERSION, ядро и входы) ===")
        app_kind = plan_app_bump(commits, fb)
        if app_kind is None:
            print(
                "  (VERSION не меняем: в диапазоне нет коммитов, затронувших core/, main.py, api.py, "
                "requirements.txt, pyproject.toml, Dockerfile — или типы коммитов не дают bump при --fallback)"
            )
        else:
            old_v = read_version_file(root)
            try:
                new_v = bump_semver(old_v, app_kind)
            except ValueError as e:
                print(f"  skip VERSION: {e}")
            else:
                print(f"  VERSION: {old_v} -> {new_v} ({app_kind})")
                if args.apply:
                    (root / "VERSION").write_text(new_v + "\n", encoding="utf-8")
                    wrote_any_version = True

    if plugin_bump_count == 0 and app_kind is None:
        print(
            "\nИтог: версии не поднимались — для `auto` без тегов смотри только последний шаг (HEAD~1..HEAD). "
            "Если последний коммит не трогал ядро/плагины, это нормально. Шире: "
            "`python scripts/auto_version_from_commits.py --since root` или `--since HEAD~10`."
        )

    if args.apply and args.sync_readme:
        import subprocess as sp

        if wrote_any_version:
            cmd = [sys.executable, str(root / "scripts/sync_versions.py")]
            if args.date:
                cmd.extend(["--date", args.date])
            sp.run(cmd, cwd=root, check=False)
        else:
            print(
                "sync_versions пропущен: VERSION и module.json не менялись. "
                "Нужны только README/дата — запусти: "
                f"python scripts/sync_versions.py{' --date ' + args.date if args.date else ''}",
            )

    if not args.apply:
        print("\n(dry-run: добавь --apply для записи файлов)")


if __name__ == "__main__":
    main()
