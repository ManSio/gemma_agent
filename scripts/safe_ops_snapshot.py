#!/usr/bin/env python3
"""
Безопасная уборка логов/ошибок + снимок состояния (бэкап перед правками).

  ./venv/bin/python scripts/safe_ops_snapshot.py --dry-run
  ./venv/bin/python scripts/safe_ops_snapshot.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env", override=False)
    except Exception:
        pass


def _behavior_dir(root: Path) -> Path:
    raw = (os.getenv("BEHAVIOR_DATA_DIR") or "data/behavior").strip()
    p = Path(raw)
    return p if p.is_absolute() else (root / p)


def _prune_jsonl_by_age(
    path: Path,
    *,
    days: int,
    dry_run: bool,
) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "pruned": False}
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    kept: List[str] = []
    dropped = 0
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except json.JSONDecodeError:
            kept.append(ln if ln.endswith("\n") else ln + "\n")
            continue
        ts = row.get("ts") or row.get("timestamp") or ""
        if ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cut:
                    dropped += 1
                    continue
            except ValueError:
                pass
        kept.append(ln if ln.endswith("\n") else ln + "\n")
    rep = {
        "path": str(path),
        "exists": True,
        "pruned": dropped > 0,
        "lines_before": len(kept) + dropped,
        "lines_after": len(kept),
        "dropped": dropped,
        "days": days,
    }
    if not dry_run and dropped > 0:
        path.write_text("".join(kept), encoding="utf-8")
    return rep


def _tail_jsonl(
    path: Path,
    *,
    max_lines: int,
    max_bytes: int,
    dry_run: bool,
) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "trimmed": False}
    size = path.stat().st_size
    if size <= max_bytes:
        return {"path": str(path), "exists": True, "trimmed": False, "size_before": size}
    rows = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    keep = rows[-max_lines:] if max_lines > 0 else rows
    rep = {
        "path": str(path),
        "exists": True,
        "trimmed": True,
        "size_before": size,
        "lines_before": len(rows),
        "lines_after": len(keep),
    }
    if not dry_run:
        path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        rep["size_after"] = path.stat().st_size
    else:
        rep["dry_run"] = True
    return rep


def _backup_files(backup_dir: Path, paths: List[Path]) -> List[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for src in paths:
        if not src.is_file():
            continue
        dest = backup_dir / src.name
        shutil.copy2(src, dest)
        copied.append(str(src.relative_to(_ROOT)) if src.is_relative_to(_ROOT) else str(src))
    beh = _behavior_dir(_ROOT)
    if beh.is_dir():
        dest = backup_dir / "behavior"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(beh, dest)
        copied.append(f"{beh.name}/")
    return copied


def _prune_autonomy_backups(root: Path, *, keep: int, dry_run: bool) -> Dict[str, Any]:
    base = root / "data" / "autonomy_backups"
    if not base.is_dir():
        return {"exists": False, "removed": 0}
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], key=lambda p: p.name, reverse=True)
    to_remove = dirs[keep:] if keep >= 0 else []
    removed: List[str] = []
    if not dry_run:
        for d in to_remove:
            shutil.rmtree(d, ignore_errors=False)
            removed.append(d.name)
    else:
        removed = [d.name for d in to_remove]
    return {"exists": True, "kept": min(len(dirs), keep), "removed": len(removed), "names": removed[:20]}


def run(*, apply: bool, days_errors: int, keep_lines: int, autonomy_keep: int) -> Dict[str, Any]:
    _load_dotenv()
    root = _ROOT
    dry_run = not apply
    stamp = _now_slug()
    backup_dir = root / "data" / "runtime" / "backups" / f"snapshot_{stamp}"
    rt = root / "data" / "runtime"

    paths_to_backup = [
        root / "data" / "runtime_errors.jsonl",
        root / "data" / "llm_usage.jsonl",
        rt / "runtime_errors.jsonl",
        rt / "llm_usage.jsonl",
        rt / "ops_trace.jsonl",
        rt / "metrics_timeseries.jsonl",
        rt / "turns.jsonl",
        rt / "ephemeral_lessons.json",
        rt / "safe_mode_state.json",
    ]

    report: Dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "stamp": stamp,
        "root": str(root),
        "behavior_dir": str(_behavior_dir(root)),
    }

    if apply:
        report["backup"] = {"dir": str(backup_dir), "files": _backup_files(backup_dir, paths_to_backup)}
    else:
        report["backup"] = {"dry_run": True, "would_dir": str(backup_dir)}

    max_bytes = int((os.getenv("HOUSEKEEPING_JSONL_MAX_BYTES") or "2097152").strip() or "2097152")
    prune_logs: List[Dict[str, Any]] = []
    for p in (root / "data" / "runtime_errors.jsonl", rt / "runtime_errors.jsonl"):
        prune_logs.append(_prune_jsonl_by_age(p, days=days_errors, dry_run=dry_run))
    for p in (
        root / "data" / "llm_usage.jsonl",
        rt / "llm_usage.jsonl",
        rt / "ops_trace.jsonl",
        rt / "metrics_timeseries.jsonl",
        rt / "experience_digest.jsonl",
        rt / "strategy_paths.jsonl",
    ):
        prune_logs.append(_tail_jsonl(p, max_lines=keep_lines, max_bytes=max_bytes, dry_run=dry_run))
    report["jsonl"] = prune_logs
    report["autonomy_backups"] = _prune_autonomy_backups(root, keep=autonomy_keep, dry_run=dry_run)

    if apply:
        from core.system_housekeeping import run_housekeeping

        report["housekeeping"] = run_housekeeping(root_path=str(root), dry_run=False)
        py = sys.executable
        cl = subprocess.run(
            [
                py,
                str(root / "scripts" / "clean_learning_runtime.py"),
                "--apply",
                "--clear-pending-quality-loop",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        report["clean_learning"] = {
            "returncode": cl.returncode,
            "stdout": (cl.stdout or "")[-2000:],
            "stderr": (cl.stderr or "")[-800:],
        }
        c6_out = root / "data" / "benchmarks" / f"c6_ab_snapshot_{stamp}.json"
        c6 = subprocess.run(
            [py, str(root / "scripts" / "analyze_brain_recent_ab.py"), "--days", "7", "--json-out", str(c6_out)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=90,
        )
        report["c6_snapshot"] = {"path": str(c6_out), "returncode": c6.returncode}
        audit_out = root / "data" / "benchmarks" / f"server_audit_{stamp}.json"
        aud = subprocess.run(
            [py, str(root / "scripts" / "server_full_audit.py"), "--json-out", str(audit_out), "--days", "7"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        report["server_audit"] = {"path": str(audit_out), "returncode": aud.returncode}
        manifest = backup_dir / "manifest.json"
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        from core.system_housekeeping import run_housekeeping

        report["housekeeping"] = run_housekeeping(root_path=str(root), dry_run=True)

    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days-errors", type=int, default=14)
    ap.add_argument("--keep-lines", type=int, default=0, help="0 = from HOUSEKEEPING_JSONL_KEEP_LINES or 6000")
    ap.add_argument("--autonomy-keep", type=int, default=3)
    args = ap.parse_args()
    apply = bool(args.apply) and not args.dry_run
    keep = args.keep_lines
    if keep <= 0:
        try:
            keep = max(100, int((os.getenv("HOUSEKEEPING_JSONL_KEEP_LINES") or "6000").strip()))
        except ValueError:
            keep = 6000
    _load_dotenv()
    if keep <= 0:
        keep = 6000
    rep = run(
        apply=apply,
        days_errors=args.days_errors,
        keep_lines=keep,
        autonomy_keep=args.autonomy_keep,
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
