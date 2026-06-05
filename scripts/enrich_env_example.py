#!/usr/bin/env python3
"""
Обогатить .env.example комментариями true/false и примерами для каждой переменной.

  python scripts/enrich_env_example.py --check     # только отчёт
  python scripts/enrich_env_example.py --write     # перезаписать .env.example
  python scripts/enrich_env_example.py --write --build-catalog
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from env_doc_generator import generate_doc_block  # noqa: E402

_KEY_LINE = re.compile(r"^(#\s*)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SECTION = re.compile(r"^# =+$")


def _parse_entries(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    i = 0
    pending_comments: list[str] = []
    pending_section: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if _SECTION.match(stripped):
            pending_section = [line]
            i += 1
            while i < len(lines) and not _KEY_LINE.match(lines[i].strip()):
                pending_section.append(lines[i])
                i += 1
            continue

        m = _KEY_LINE.match(stripped)
        if m:
            commented = bool(m.group(1))
            key = m.group(2)
            val = m.group(3)
            entries.append(
                {
                    "key": key,
                    "value": val,
                    "commented": commented,
                    "comments": list(pending_comments),
                    "section": list(pending_section),
                    "raw_line": line,
                }
            )
            pending_comments = []
            pending_section = []
            i += 1
            continue

        if stripped.startswith("#") or not stripped:
            pending_comments.append(line)
        else:
            pending_comments.append(line)
        i += 1

    return entries


def _render_entry(ent: dict, *, enrich: bool) -> list[str]:
    key = ent["key"]
    val = ent["value"]
    commented = ent["commented"]
    comments = ent["comments"]

    if enrich:
        doc = generate_doc_block(key, val, [c for c in comments if c.strip().startswith("#")])
        # Убираем старые однострочные # --- если заменяем полным блоком
        if doc and comments and all(
            c.strip().startswith("# ---") or not c.strip() for c in comments
        ):
            comments = []
        comments = doc + ([""] if doc else [])

    # В example дефолт часто был «# KEY=val» — в рабочий .env нужна активная строка.
    active = (not commented) or bool((val or "").strip())
    prefix = "" if active else "# "
    body = f"{prefix}{key}={val}" if active or not val else f"# {key}="
    return list(ent.get("section") or []) + comments + [body]


def enrich_file(path: Path, *, write: bool) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    entries = _parse_entries(lines)

  # preamble до первой секции/ключа
    preamble: list[str] = []
    for line in lines:
        if _KEY_LINE.match(line.strip()) or _SECTION.match(line.strip()):
            break
        preamble.append(line)

    out_lines: list[str] = list(preamble)
    if out_lines and out_lines[-1].strip():
        out_lines.append("")

    enriched_count = 0
    for ent in entries:
        old_comments = [c for c in ent["comments"] if c.strip().startswith("#")]
        new_block = _render_entry(ent, enrich=True)
        new_comments = [c for c in new_block if c.strip().startswith("#")]
        if len(new_comments) > len(old_comments) + 1:
            enriched_count += 1
        out_lines.extend(new_block)
        out_lines.append("")

    footer = [
        "# =============================================================================",
        "# Конец. Секреты не в git. Полная синхронизация значений:",
        "#   python scripts/sync_env_from_example.py",
        "# Каталог фрагментов: python scripts/enrich_env_example.py --write --build-catalog",
        "# =============================================================================",
    ]
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    out_lines.extend(footer)
    out_lines.append("")

    if write:
        path.write_text("\n".join(out_lines), encoding="utf-8")

    return len(entries), enriched_count


def build_catalog_fragments(example_path: Path, catalog_dir: Path) -> int:
    """Разбить .env.example на фрагменты по секциям для apply_env_catalog."""
    catalog_dir.mkdir(parents=True, exist_ok=True)
    for old in catalog_dir.glob("*.env.fragment"):
        old.unlink()

    lines = example_path.read_text(encoding="utf-8", errors="replace").splitlines()
    section_title = "00_header"
    section_lines: list[str] = []
    file_idx = 0
    files_written = 0

    def flush() -> None:
        nonlocal section_lines, file_idx, files_written
        if not section_lines:
            return
        slug = re.sub(r"[^a-z0-9]+", "_", section_title.lower())[:48].strip("_") or "block"
        path = catalog_dir / f"{file_idx:02d}_{slug}.env.fragment"
        path.write_text("\n".join(section_lines).rstrip() + "\n", encoding="utf-8")
        files_written += 1
        file_idx += 1
        section_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if lines[i].strip() == "# =============================================================================":
            if i + 1 < len(lines) and not _KEY_LINE.match(lines[i + 1].strip()):
                flush()
                title_line = lines[i + 1] if i + 1 < len(lines) else "section"
                section_title = title_line.strip("# ").strip() or "section"
                section_lines.append(line)
                i += 1
                if i < len(lines):
                    section_lines.append(lines[i])
                    i += 1
                if i < len(lines) and lines[i].strip() == "# =============================================================================":
                    section_lines.append(lines[i])
                    i += 1
                continue
        section_lines.append(line)
        i += 1
    flush()
    return files_written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="Записать .env.example")
    ap.add_argument("--build-catalog", action="store_true", help="Пересобрать config/env_catalog/generated/")
    ap.add_argument("--path", default=str(_ROOT / ".env.example"))
    args = ap.parse_args()
    path = Path(args.path)
    if not path.is_file():
        print(f"[ERR] not found: {path}", file=sys.stderr)
        return 1

    total, enriched = enrich_file(path, write=args.write)
    mode = "written" if args.write else "check"
    print(f"[OK] {path}: {mode}, variables={total}, enriched_blocks={enriched}")

    if args.build_catalog and args.write:
        cat = _ROOT / "config" / "env_catalog" / "generated"
        n = build_catalog_fragments(path, cat)
        print(f"[OK] catalog fragments: {n} files in {cat}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
