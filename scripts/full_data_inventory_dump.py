#!/usr/bin/env python3
"""
Полный инвентарь данных на диске: пути, размеры, число строк (JSONL/логи), каталоги.

Зачем: не путать «пустой turns.jsonl» с «бот не работал» — см. docs/CONVERSATION_LOGS_MAP_RU.md.
Для окна активности по времени после инвентаря: scripts/day_conversation_audit.py

  python scripts/full_data_inventory_dump.py
  python scripts/full_data_inventory_dump.py --root /opt/gemma_agent
  python scripts/full_data_inventory_dump.py --root /opt/gemma_agent --json-out /tmp/inventory.json
  python scripts/full_data_inventory_dump.py --fast   # без подсчёта строк (только размеры)

Переменные окружения (как у процесса бота) влияют на резолв путей: GEMMA_*_PATH, BEHAVIOR_DATA_DIR, GEMMA_DATA_DIR.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class FileRow:
    label: str
    path: str
    exists: bool
    bytes: int
    lines: Optional[int]
    line_note: str
    mtime_utc: str


def _resolve(root: Path, raw: str) -> Path:
    p = Path(raw.strip())
    if p.is_absolute():
        return p.resolve()
    return (root / p).resolve()


def _count_newlines(path: Path, max_bytes: int) -> Tuple[Optional[int], str]:
    try:
        size = path.stat().st_size
    except OSError as e:
        return None, f"stat: {e}"
    if size > max_bytes:
        return None, f"skip_lines size>{max_bytes}"
    n = 0
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += chunk.count(b"\n")
    except OSError as e:
        return None, f"read: {e}"
    return n, ""


def _stat_file(label: str, path: Path, *, count_lines: bool, max_bytes: int) -> FileRow:
    exists = path.is_file()
    if not exists:
        return FileRow(label, str(path), False, 0, None, "missing", "")
    try:
        st = path.stat()
        b = int(st.st_size)
        mt = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    except OSError as e:
        return FileRow(label, str(path), True, 0, None, f"stat_err:{e}", "")
    lines: Optional[int] = None
    note = ""
    if count_lines and b > 0:
        lines, note = _count_newlines(path, max_bytes)
        if lines is None and not note:
            note = "no_lines"
    elif not count_lines:
        note = "fast_mode"
    return FileRow(label, str(path), True, b, lines, note, mt)


def _dir_summary(path: Path, *, pattern: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": str(path), "exists": path.is_dir()}
    if not path.is_dir():
        return out
    files = list(path.glob(pattern))
    total_b = 0
    for f in files:
        try:
            total_b += f.stat().st_size
        except OSError:
            pass
    out["n_files"] = len(files)
    out["total_bytes"] = total_b
    return out


def _collect_file_specs(root: Path, data: Path) -> List[Tuple[str, Path]]:
    """(label, path) — путь с учётом типичных env (читаются в момент запуска скрипта)."""
    rows: List[Tuple[str, Path]] = []

    def add(label: str, p: Path) -> None:
        rows.append((label, p))

    # Явные env (как в коде ядра)
    env_map = [
        ("GEMMA_TURNS_LOG_PATH", None),
        ("GEMMA_OPS_TRACE_PATH", None),
        ("GEMMA_LLM_USAGE_PATH", None),
        ("GEMMA_ROUTE_RISK_PATH", None),
        ("GEMMA_STRATEGY_PATH", None),
        ("GEMMA_SESSION_DIGEST_PATH", None),
        ("GEMMA_KV_DEBUG_LOG_PATH", None),
        ("GEMMA_CDC_AGGREGATES_PATH", None),
        ("GEMMA_EXPERIENCE_PATH", None),
        ("GEMMA_LOG_FILE", None),
        ("LOG_FILE", None),
    ]
    for env, _ in env_map:
        raw = (os.getenv(env) or "").strip()
        if raw:
            add(f"env:{env}", _resolve(root, raw))

    rt = data / "runtime"
    # Два варианта llm_usage
    add("llm_usage.jsonl (runtime)", rt / "llm_usage.jsonl")
    add("llm_usage.jsonl (data root)", data / "llm_usage.jsonl")
    add("turns.jsonl", rt / "turns.jsonl")
    add("ops_trace.jsonl", rt / "ops_trace.jsonl")
    add("route_risk.jsonl (default)", rt / "route_risk.jsonl")
    add("strategy_paths.jsonl (default)", rt / "strategy_paths.jsonl")
    add("experience_digest.jsonl", rt / "experience_digest.jsonl")
    add("cdc_turn_outcomes.jsonl", rt / "cdc_turn_outcomes.jsonl")
    add("metrics_timeseries.jsonl", rt / "metrics_timeseries.jsonl")
    add("quality_audit.jsonl", rt / "quality_audit.jsonl")
    add("kv_debug.jsonl (default)", rt / "kv_debug.jsonl")
    add("runtime_errors.jsonl (data/)", data / "runtime_errors.jsonl")
    add("runtime_errors.jsonl (runtime/)", rt / "runtime_errors.jsonl")

    beh_root = (os.getenv("BEHAVIOR_DATA_DIR") or "").strip()
    if beh_root:
        br = _resolve(root, beh_root)
    else:
        br = data / "users"
    add("behavior_store dir", br / "behavior")
    add("group_transcripts dir", br / "group_transcripts")
    add("logs/gemma_bot.log (data/logs)", data / "logs" / "gemma_bot.log")
    add("logs/gemma_bot.log (BEHAVIOR..logs)", br / "logs" / "gemma_bot.log")

    return rows


def _dedupe_specs(specs: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    """Один путь — один раз, сохраняем первый label."""
    by_key: Dict[str, Tuple[str, Path]] = {}
    for label, p in specs:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except OSError:
            key = str(p)
        if key not in by_key:
            by_key[key] = (label, p)
    return list(by_key.values())


def main() -> int:
    ap = argparse.ArgumentParser(description="Full on-disk data inventory for Gemma bot")
    ap.add_argument("--root", default=str(ROOT), help="Project root (sets GEMMA_PROJECT_ROOT if unset)")
    ap.add_argument("--fast", action="store_true", help="Only file sizes, no line counting")
    ap.add_argument("--max-count-bytes", type=int, default=80 * 1024 * 1024, help="Max file size to count lines")
    ap.add_argument("--json-out", default="", help="Write full report JSON to this path")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not os.getenv("GEMMA_PROJECT_ROOT"):
        os.environ["GEMMA_PROJECT_ROOT"] = str(root)

    data_raw = (os.getenv("GEMMA_DATA_DIR") or "").strip()
    data = Path(data_raw).resolve() if data_raw else (root / "data")

    print("=== full_data_inventory_dump ===")
    print(f"ts_utc: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"root: {root}")
    print(f"data: {data}")
    print(f"GEMMA_PROJECT_ROOT: {os.getenv('GEMMA_PROJECT_ROOT')}")
    print(f"BEHAVIOR_DATA_DIR: {os.getenv('BEHAVIOR_DATA_DIR') or '(default data/users under data)'}")
    print()

    specs = _dedupe_specs(_collect_file_specs(root, data))

    file_rows: List[FileRow] = []
    for label, p in specs:
        if p.is_dir():
            continue
        file_rows.append(
            _stat_file(
                label,
                p,
                count_lines=not args.fast,
                max_bytes=int(args.max_count_bytes),
            )
        )

    rt = data / "runtime"
    try:
        rt_r = rt.resolve()
    except OSError:
        rt_r = rt
    listed_runtime_jsonl: set[str] = set()
    for r in file_rows:
        pp = Path(r.path)
        try:
            if pp.is_file() and pp.parent.resolve() == rt_r and pp.suffix == ".jsonl":
                listed_runtime_jsonl.add(pp.name)
        except OSError:
            pass

    print("--- files (JSONL / logs) ---")
    for r in sorted(file_rows, key=lambda x: (not x.exists, x.label)):
        ln = "—" if r.lines is None else str(r.lines)
        note = f" [{r.line_note}]" if r.line_note else ""
        ex = "yes" if r.exists else "NO"
        print(f"  [{ex}] {r.label}")
        print(f"       path: {r.path}")
        print(f"       bytes: {r.bytes}  lines: {ln}{note}  mtime_utc: {r.mtime_utc or '—'}")

    # runtime: все *.jsonl не из списка
    extras: List[str] = []
    if rt.is_dir():
        for fp in sorted(rt.glob("*.jsonl")):
            if fp.name in listed_runtime_jsonl:
                continue
            extras.append(fp.name)

    if extras:
        print()
        print("--- data/runtime/*.jsonl (extra, not in core list) ---")
        for name in extras[:80]:
            fp = rt / name
            st = fp.stat()
            nl, nn = _count_newlines(fp, int(args.max_count_bytes)) if not args.fast else (None, "fast_mode")
            line_s = "—" if nl is None else str(nl)
            print(f"  • {name}  bytes={st.st_size}  lines={line_s}  {nn}")
        if len(extras) > 80:
            print(f"  … +{len(extras) - 80} more")

    print()
    print("--- directories ---")
    beh = data / "users" / "behavior"
    if not beh.is_dir():
        beh = data / "behavior"
    gtr = data / "users" / "group_transcripts"
    for label, d in (
        ("behavior_store (*.json)", beh),
        ("group_transcripts (*.json)", gtr),
        ("data/autonomy_backups", data / "autonomy_backups"),
        ("data/cache", data / "cache"),
    ):
        s = _dir_summary(d, pattern="*.json") if "behavior" in label or "group" in label else _dir_summary(d, pattern="*")
        print(f"  {label}: {json.dumps(s, ensure_ascii=False)}")

    print()
    print("--- how to read this ---")
    print("  • Полный текст диалога: behavior_store JSON (recent_messages), не только turns.jsonl.")
    print("  • turns/ops могут быть редкими, если ход не прошёл условия оркестратора (plan.steps, user_payload).")
    print("  • Окно по времени (UTC): python scripts/day_conversation_audit.py --hours 24 --root " + str(root))
    print("  • Метрики/хвосты: python scripts/system_audit.py --data-dir " + str(data))
    print("  • Документация: docs/CONVERSATION_LOGS_MAP_RU.md")

    report: Dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "data": str(data),
        "files": [asdict(r) for r in file_rows],
        "runtime_extra_jsonl": extras,
        "dirs": {
            "behavior_store": _dir_summary(beh, pattern="*.json"),
            "group_transcripts": _dir_summary(gtr, pattern="*.json"),
        },
    }
    if args.json_out:
        outp = Path(args.json_out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(f"wrote: {outp}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
