#!/usr/bin/env python3
"""
Очистка мусора в runtime-обучении: дубликаты quality_loop, probe-шум, pending_correction.

Не изолирует каналы — убирает повторы и автогенерированный шум, бэкап перед правкой.

  ./venv/bin/python scripts/clean_learning_runtime.py --dry-run
  ./venv/bin/python scripts/clean_learning_runtime.py --apply
  ./venv/bin/python scripts/clean_learning_runtime.py --apply --clear-pending-quality-loop
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Известный probe-шум (Telegram suite / manual probe) — не учебные кейсы
_PROBE_NOISE_PATTERNS = (
    re.compile(r"(?i)samsung\s*s26"),
    re.compile(r"(?i)как\s+тебя\s+зовут"),
    re.compile(r"(?i)@example_test_bot"),
)

_RUNTIME = _ROOT / "data" / "runtime"


def _behavior_dir() -> Path:
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env", override=False)
    except Exception:
        pass
    raw = (os.getenv("BEHAVIOR_DATA_DIR") or "data/behavior").strip()
    p = Path(raw)
    return p if p.is_absolute() else (_ROOT / p)


def _behavior_glob() -> Iterable[Path]:
    beh = _behavior_dir()
    if not beh.is_dir():
        return []
    return beh.glob("*.json")


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return rows


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")


def _row_text(row: Dict[str, Any]) -> str:
    return (
        str(row.get("user_excerpt") or row.get("user_text") or row.get("text") or "")
        .strip()
    )


def _row_issues(row: Dict[str, Any]) -> Tuple[str, ...]:
    raw = row.get("issues") or row.get("errors") or []
    if isinstance(raw, str):
        raw = [raw]
    return tuple(sorted(str(x) for x in raw if x))


def _dedupe_key(row: Dict[str, Any]) -> Tuple[str, str, str, Tuple[str, ...]]:
    return (
        str(row.get("source") or row.get("test_id") or ""),
        str(row.get("user_id") or ""),
        _row_text(row)[:120],
        _row_issues(row),
    )


def _is_probe_noise(row: Dict[str, Any]) -> bool:
    text = _row_text(row)
    if not text:
        return False
    return any(p.search(text) for p in _PROBE_NOISE_PATTERNS)


def dedupe_jsonl(rows: List[Dict[str, Any]], *, drop_probe_noise: bool) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Оставить последнюю запись на ключ; опционально выкинуть probe-шум."""
    stats = Counter()
    by_key: Dict[Tuple[str, str, str, Tuple[str, ...]], Dict[str, Any]] = {}
    order: List[Tuple[str, str, str, Tuple[str, ...]]] = []
    for row in rows:
        if drop_probe_noise and _is_probe_noise(row):
            stats["dropped_probe_noise"] += 1
            continue
        key = _dedupe_key(row)
        if key not in by_key:
            order.append(key)
        else:
            stats["deduped"] += 1
        by_key[key] = row
    out = [by_key[k] for k in order]
    stats["kept"] = len(out)
    stats["input"] = len(rows)
    return out, dict(stats)


def backup_runtime(backup_dir: Path) -> List[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for name in (
        "agent_test_lessons.jsonl",
        "quality_audit.jsonl",
        "ephemeral_lessons.json",
        "route_risk.jsonl",
    ):
        src = _RUNTIME / name
        if src.is_file():
            shutil.copy2(src, backup_dir / name)
            copied.append(name)
    beh = _behavior_dir()
    if beh.is_dir():
        dest = backup_dir / "behavior"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(beh, dest)
        copied.append("behavior/")
    return copied


def clean_ephemeral(*, drop_quality_loop: bool, dry_run: bool) -> Dict[str, int]:
    from core.ephemeral_lessons import load_document, lessons_path

    doc = load_document()
    lessons = doc.get("lessons") or []
    kept: List[Dict[str, Any]] = []
    stats = Counter()
    for le in lessons:
        if not isinstance(le, dict):
            stats["skipped"] += 1
            continue
        src = str(le.get("source") or "")
        trig = str(le.get("trigger") or le.get("match") or "")
        if drop_quality_loop and src == "quality_loop":
            stats["removed_quality_loop"] += 1
            continue
        if drop_quality_loop and any(p.search(trig) for p in _PROBE_NOISE_PATTERNS):
            stats["removed_probe_trigger"] += 1
            continue
        if not le.get("active", True):
            stats["inactive_kept"] += 1
        kept.append(le)
    stats["active_before"] = sum(1 for x in lessons if isinstance(x, dict) and x.get("active", True))
    stats["active_after"] = sum(1 for x in kept if isinstance(x, dict) and x.get("active", True))
    if not dry_run:
        doc["lessons"] = kept
        path = lessons_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return dict(stats)


def clear_quality_loop_pending(*, dry_run: bool) -> Dict[str, int]:
    from core.behavior_store import BehaviorStore

    stats = Counter()
    for fp in _behavior_glob():
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rp = raw.get("routing_prefs") if isinstance(raw, dict) else {}
        if not isinstance(rp, dict):
            continue
        pending = rp.get("pending_correction")
        if not isinstance(pending, dict):
            continue
        if str(pending.get("source") or "") != "quality_loop":
            continue
        stats["pending_found"] += 1
        if dry_run:
            continue
        rp.pop("pending_correction", None)
        raw["routing_prefs"] = rp
        fp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        stats["pending_cleared"] += 1
    return dict(stats)


def promote_golden_from_report(report_path: Path, out_path: Path, *, dry_run: bool) -> int:
    """PASS из agent_test report → golden_corpus (качественные Q+A для ревью)."""
    if not report_path.is_file():
        return 0
    existing: Set[str] = set()
    if out_path.is_file():
        for row in _read_jsonl(out_path):
            existing.add(str(row.get("id") or ""))
    added = 0
    new_rows: List[Dict[str, Any]] = []
    for row in _read_jsonl(report_path):
        if not row.get("pass"):
            continue
        cid = str(row.get("id") or "")
        if not cid or cid in existing:
            continue
        new_rows.append(
            {
                "id": cid,
                "ts": row.get("ts"),
                "source": row.get("source") or "agent_test_pass",
                "user_text": row.get("user_text"),
                "reply_preview": row.get("reply_preview"),
                "tags": row.get("tags") or [],
                "status": "golden_candidate",
            }
        )
        added += 1
    if new_rows and not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean learning runtime garbage")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="Only print stats")
    ap.add_argument("--drop-probe-noise", action="store_true", default=True)
    ap.add_argument("--no-drop-probe-noise", action="store_false", dest="drop_probe_noise")
    ap.add_argument("--clear-pending-quality-loop", action="store_true")
    ap.add_argument("--ephemeral-drop-quality-loop", action="store_true", default=True)
    ap.add_argument("--no-ephemeral-drop-quality-loop", action="store_false", dest="ephemeral_drop_quality_loop")
    ap.add_argument(
        "--promote-report",
        default="",
        help="Path to agent_test jsonl report → append PASS to data/learning/golden_corpus.jsonl",
    )
    args = ap.parse_args()
    dry_run = not args.apply or args.dry_run

    print(f"mode={'DRY-RUN' if dry_run else 'APPLY'} root={_ROOT}")

    if args.apply and not args.dry_run:
        backup = _RUNTIME / "backups" / f"clean_{_now_slug()}"
        copied = backup_runtime(backup)
        print(f"backup → {backup} ({', '.join(copied) or 'empty'})")

    summary: Dict[str, Any] = {}

    for fname in ("agent_test_lessons.jsonl", "quality_audit.jsonl"):
        path = _RUNTIME / fname
        rows = _read_jsonl(path)
        cleaned, st = dedupe_jsonl(rows, drop_probe_noise=args.drop_probe_noise)
        summary[fname] = st
        print(f"{fname}: in={st['input']} kept={st['kept']} deduped={st.get('deduped',0)} probe_drop={st.get('dropped_probe_noise',0)}")
        if not dry_run and path.is_file():
            _write_jsonl(path, cleaned)

    ep_st = clean_ephemeral(drop_quality_loop=args.ephemeral_drop_quality_loop, dry_run=dry_run)
    summary["ephemeral_lessons"] = ep_st
    print(f"ephemeral: {ep_st}")

    if args.clear_pending_quality_loop:
        pend = clear_quality_loop_pending(dry_run=dry_run)
        summary["pending_correction"] = pend
        print(f"pending_correction: {pend}")

    if args.promote_report:
        n = promote_golden_from_report(
            Path(args.promote_report),
            _ROOT / "data" / "learning" / "golden_corpus.jsonl",
            dry_run=dry_run,
        )
        summary["golden_promoted"] = n
        print(f"golden_corpus +{n} from {args.promote_report}")

    report_path = _RUNTIME / "backups" / f"clean_{_now_slug()}_summary.json"
    if not dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"summary → {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
