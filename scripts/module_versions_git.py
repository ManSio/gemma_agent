#!/usr/bin/env python3
"""
Версии модулей (module.json → поле version) с опорой на git и журнал «было → стало».

report — таблица: модуль, текущая version, последний коммит, затронут ли деревом изменений (если не задан --since).
bump — поднять semver у выбранных модулей и дописать строку в docs/MODULE_VERSION_HISTORY.md.

Рабочее дерево vs HEAD (по умолчанию для --git-changes):
  - учитываются изменённые/индексированные файлы и неотслеживаемые под modules/ и core_libraries/.

Сравнение с референсом (--since REF):
  - git diff --name-only REF HEAD — что поменялось в коммитах после REF.

Правки только bundled_with в module.json не считаются изменением модуля (см. sync_versions.py).

audit — сколько коммитов касалось папки модуля за историю ветки (или с --from REF до HEAD),
  первая/последняя дата в git; «какая версия должна быть» semver не выводится автоматически —
  смотри коммиты и политику релиза (major/minor/patch вручную), затем bump.

Примеры:
  python scripts/module_versions_git.py report
  python scripts/module_versions_git.py audit
  python scripts/module_versions_git.py audit --from v2.0.0
  python scripts/module_versions_git.py audit --filter rag
  python scripts/module_versions_git.py bump --git-changes --patch --dry-run
  python scripts/module_versions_git.py bump --since v2.0.0 --minor --dry-run
  python scripts/module_versions_git.py bump --module rag --module echo --patch
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.release_conventional import bump_semver


def repo_root() -> Path:
    return _REPO_ROOT


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Явный UTF-8: на Windows иначе text=True берёт cp1251 и git log/show ломаются на кириллице в сообщениях.
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
    )


def git_toplevel(root: Path) -> Path | None:
    p = run_git(root, "rev-parse", "--show-toplevel", check=False)
    if p.returncode != 0:
        return None
    return Path(p.stdout.strip())


def strip_bundled(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "bundled_with"}


def manifest_needs_version_bump(old: dict | None, new: dict) -> bool:
    """True если манифест изменился не только полем bundled_with."""
    if old is None:
        return True
    return strip_bundled(old) != strip_bundled(new)


def module_root_for_path(path: str) -> str | None:
    path = path.replace("\\", "/").strip()
    if not path:
        return None
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in ("modules", "core_libraries"):
        return f"{parts[0]}/{parts[1]}"
    return None


def iter_module_json(root: Path) -> list[Path]:
    out: list[Path] = []
    for pat in ("modules/*/module.json", "core_libraries/*/module.json"):
        out.extend(sorted(root.glob(pat)))
    return out


def normalize_changed_files(lines: Iterable[str]) -> set[str]:
    return {ln.strip().replace("\\", "/") for ln in lines if ln.strip()}


def git_changed_paths(root: Path, since: str | None) -> set[str]:
    out: set[str] = set()
    if since:
        d = run_git(root, "diff", "--name-only", since, "HEAD")
        out |= normalize_changed_files(d.stdout.splitlines())
        return out
    d1 = run_git(root, "diff", "--name-only", "HEAD")
    out |= normalize_changed_files(d1.stdout.splitlines())
    d2 = run_git(root, "diff", "--cached", "--name-only", "HEAD")
    out |= normalize_changed_files(d2.stdout.splitlines())
    others = run_git(root, "ls-files", "--others", "--exclude-standard", "modules", "core_libraries")
    out |= normalize_changed_files(others.stdout.splitlines())
    return out


def paths_by_module_root(changed: set[str]) -> dict[str, list[str]]:
    by: dict[str, list[str]] = {}
    for p in changed:
        root = module_root_for_path(p)
        if not root:
            continue
        by.setdefault(root, []).append(p)
    return by


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def git_show_json(root: Path, rev: str, rel: str) -> dict | None:
    p = run_git(root, "show", f"{rev}:{rel}", check=False)
    if p.returncode != 0 or not (p.stdout or "").strip():
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def module_should_bump(
    root: Path,
    module_root: str,
    files_under: list[str],
    *,
    since: str | None,
) -> bool:
    rel_manifest = f"{module_root}/module.json"
    manifest_path = root / rel_manifest
    under = [fn.replace("\\", "/") for fn in files_under if fn.replace("\\", "/").startswith(module_root + "/")]
    if not under:
        return False
    non_manifest = [fn for fn in under if fn != rel_manifest]
    if non_manifest:
        return True
    if rel_manifest not in under:
        return False
    if since:
        old = git_show_json(root, since, rel_manifest)
        new = git_show_json(root, "HEAD", rel_manifest)
        if new is None:
            return False
        return manifest_needs_version_bump(old, new)
    if not manifest_path.is_file():
        return True
    old = git_show_json(root, "HEAD", rel_manifest)
    new = read_json(manifest_path)
    return manifest_needs_version_bump(old, new)


def last_commit_for_path(root: Path, rel_dir: str) -> str:
    p = run_git(
        root,
        "log",
        "-1",
        "--format=%cs %h %s",
        "--",
        rel_dir,
        check=False,
    )
    if p.returncode != 0:
        return "—"
    s = (p.stdout.strip() or "—").replace("|", "·")
    return s


def git_commit_count(root: Path, rel_path: str, from_ref: str | None) -> int:
    """Число коммитов, затронувших путь: вся история до HEAD или диапазон from_ref..HEAD."""
    spec = f"{from_ref.strip()}..HEAD" if (from_ref or "").strip() else "HEAD"
    p = run_git(root, "rev-list", "--count", spec, "--", rel_path, check=False)
    if p.returncode != 0:
        return 0
    try:
        return int((p.stdout or "").strip() or "0")
    except ValueError:
        return 0


def git_first_last_dates(root: Path, rel_path: str) -> tuple[str, str]:
    """Первая и последняя дата коммита, коснувшегося пути (%cs)."""
    fa = run_git(root, "log", "--reverse", "-1", "--format=%cs", "--", rel_path, check=False)
    fb = run_git(root, "log", "-1", "--format=%cs", "--", rel_path, check=False)
    a = (fa.stdout or "").strip() if fa.returncode == 0 else ""
    b = (fb.stdout or "").strip() if fb.returncode == 0 else ""
    return (a or "—", b or "—")


def cmd_audit(root: Path, *, from_ref: str | None, path_filter: str) -> None:
    pf = (path_filter or "").strip().lower()
    fr = (from_ref or "").strip()
    if fr:
        print(f"| Модуль | version | коммитов (вся ветка) | коммитов {fr}..HEAD | первая дата | последняя дата |")
        print("|--------|---------|------------------------|----------------------|-------------|----------------|")
    else:
        print("| Модуль | version | коммитов (вся ветка) | первая дата | последняя дата |")
        print("|--------|---------|------------------------|-------------|----------------|")
    for mj in iter_module_json(root):
        rel = mj.parent.relative_to(root).as_posix()
        if pf and pf not in rel.lower():
            continue
        data = read_json(mj)
        ver = str(data.get("version", "?"))
        total = git_commit_count(root, rel, None)
        d0, d1 = git_first_last_dates(root, rel)
        if fr:
            since_n = git_commit_count(root, rel, from_ref)
            print(f"| `{rel}` | `{ver}` | {total} | {since_n} | {d0} | {d1} |")
        else:
            print(f"| `{rel}` | `{ver}` | {total} | {d0} | {d1} |")
    print()
    print(
        "Семвер модуля (поле version) git сам не знает: major = ломающие изменения, "
        "minor = новые возможности, patch = исправления. Посмотреть историю папки:\n"
        "  git log --oneline --first-parent -- modules/ИМЯ/\n"
        "С начала репозитория на этой ветке (корневой коммит): git rev-list --max-parents=0 HEAD\n"
        "  python scripts/module_versions_git.py audit --from <этот_хеш>",
    )


def cmd_report(root: Path, since: str | None) -> None:
    changed = git_changed_paths(root, since)
    by_changed = paths_by_module_root(changed)
    scope = f"REF..HEAD ({since})" if since else "рабочее дерево vs HEAD"
    print(f"| Модуль | version | последний коммит (папка) | затронут ({scope}) |")
    print("|--------|---------|--------------------------|---------------------------|")
    for mj in iter_module_json(root):
        rel = mj.parent.relative_to(root).as_posix()
        data = read_json(mj)
        ver = data.get("version", "?")
        lc = last_commit_for_path(root, rel)
        roots = by_changed.get(rel, [])
        if not roots:
            touched = "нет"
        elif module_should_bump(root, rel, roots, since=since):
            touched = "да"
        else:
            touched = "нет (только bundled_with / без смысловых правок манифеста)"
        print(f"| `{rel}` | `{ver}` | {lc} | {touched} |")


HISTORY_PATH = "docs/MODULE_VERSION_HISTORY.md"
HISTORY_HEADER = """# История версий модулей

Колонка **Было** — semver в `module.json` до bump; **Стало** — после. Записи добавляют `module_versions_git bump` и `auto_version_from_commits --apply`.

| UTC дата | Модуль | Было | Стало | commit | примечание |
|----------|--------|------|-------|--------|------------|
"""


def append_history(
    root: Path,
    module_rel: str,
    was: str,
    new: str,
    commit_hint: str,
    note: str,
) -> None:
    path = root / HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(HISTORY_HEADER, encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        if "UTC дата" not in text:
            path.write_text(HISTORY_HEADER, encoding="utf-8")
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    safe = lambda s: s.replace("|", "\\|")
    row = f"| {when} | `{safe(module_rel)}` | `{safe(was)}` | `{safe(new)}` | `{safe(commit_hint)}` | {safe(note)} |\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(row)


def cmd_bump(
    root: Path,
    *,
    modules: list[str],
    kind: str,
    dry_run: bool,
    git_changes: bool,
    since: str | None,
) -> None:
    to_bump: set[str] = set()
    if modules:
        for m in modules:
            m = m.strip().strip("/").replace("\\", "/")
            if not m.startswith(("modules/", "core_libraries/")):
                m = f"modules/{m}"
            if not (root / m / "module.json").is_file():
                print(f"skip: нет module.json для {m}", file=sys.stderr)
                continue
            to_bump.add(m)
    if git_changes:
        changed = git_changed_paths(root, since)
        by = paths_by_module_root(changed)
        for mod_root, files in sorted(by.items()):
            if module_should_bump(root, mod_root, files, since=since):
                to_bump.add(mod_root)
    if not to_bump:
        print("Нет модулей для bump (проверьте --module или изменения в git).")
        return
    ref = ""
    try:
        r = run_git(root, "rev-parse", "--short", "HEAD")
        ref = r.stdout.strip()
    except Exception:
        ref = "?"
    for mod_root in sorted(to_bump):
        mj = root / mod_root / "module.json"
        data = read_json(mj)
        old_v = str(data.get("version", "0.0.0"))
        try:
            new_v = bump_semver(old_v, kind)
        except ValueError as e:
            print(f"{mod_root}: {e}", file=sys.stderr)
            continue
        if dry_run:
            print(f"[dry-run] {mod_root}: {old_v} -> {new_v} ({kind})")
            continue
        data["version"] = new_v
        mj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        append_history(root, mod_root, old_v, new_v, ref, kind)
        print(f"updated {mod_root}: {old_v} -> {new_v}")
    if dry_run:
        print("(dry-run: файлы не менялись)")


def main() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    root = repo_root()
    top = git_toplevel(root)
    if top is None or top.resolve() != root.resolve():
        raise SystemExit("Запускайте из корня git-репозитория (ожидается совпадение с корнем проекта).")

    ap = argparse.ArgumentParser(description="Версии модулей по git + журнал")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="Таблица модулей и свежести git")
    r.add_argument(
        "--since",
        default=None,
        metavar="REF",
        help="Сравнивать с REF (git diff REF HEAD); иначе — рабочее дерево vs HEAD",
    )

    au = sub.add_parser("audit", help="История git по папке модуля (коммиты, даты) — основа для semver вручную")
    au.add_argument(
        "--from",
        dest="from_ref",
        default=None,
        metavar="REF",
        help="Доп. колонка: число коммитов в диапазоне REF..HEAD (тег, хеш, HEAD~10)",
    )
    au.add_argument("--filter", default="", help="Подстрока в пути модуля, например rag")

    b = sub.add_parser("bump", help="Повысить version в module.json")
    b.add_argument("--module", action="append", dest="modules", default=[], help="Каталог modules/foo или core_libraries/bar")
    b.add_argument("--git-changes", action="store_true", help="Все модули с значимыми изменениями по git")
    b.add_argument(
        "--since",
        default=None,
        metavar="REF",
        help="С --git-changes: имена файлов из git diff REF HEAD",
    )
    mx = b.add_mutually_exclusive_group(required=True)
    mx.add_argument("--patch", action="store_const", dest="kind", const="patch")
    mx.add_argument("--minor", action="store_const", dest="kind", const="minor")
    mx.add_argument("--major", action="store_const", dest="kind", const="major")
    b.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.cmd == "report":
        since = getattr(args, "since", None)
        cmd_report(root, since)
        return
    if args.cmd == "audit":
        cmd_audit(root, from_ref=getattr(args, "from_ref", None), path_filter=getattr(args, "filter", "") or "")
        return
    if args.cmd == "bump":
        if not args.modules and not args.git_changes:
            raise SystemExit("Укажите --module … и/или --git-changes")
        cmd_bump(
            root,
            modules=args.modules or [],
            kind=args.kind,
            dry_run=args.dry_run,
            git_changes=args.git_changes,
            since=args.since,
        )
        return


if __name__ == "__main__":
    main()
