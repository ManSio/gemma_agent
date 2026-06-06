from __future__ import annotations

import os
import sqlite3
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int((os.getenv(name) or "").strip() or str(default)))
    except (TypeError, ValueError):
        return default


def _env_set(name: str) -> bool:
    return (os.getenv(name) or "").strip() != ""


def housekeeping_enabled() -> bool:
    return _truthy("HOUSEKEEPING_ENABLED", True)


def _runtime_noise_bytes(root: Path) -> int:
    rt = root / "data" / "runtime"
    if not rt.is_dir():
        return 0
    names = (
        "llm_usage.jsonl",
        "session_digest.jsonl",
        "route_risk.jsonl",
        "experience_digest.jsonl",
        "strategy_paths.jsonl",
        "runtime_errors.jsonl",
    )
    total = 0
    for n in names:
        p = rt / n
        try:
            if p.is_file():
                total += int(p.stat().st_size)
        except Exception:
            continue
    return total


def _resolve_housekeeping_profile(root: Path) -> str:
    forced = (os.getenv("HOUSEKEEPING_PROFILE") or "auto").strip().lower()
    if forced in {"safe", "balanced", "aggressive"}:
        return forced
    # auto mode
    try:
        du = shutil.disk_usage(root)
        used_pct = (float(du.used) / float(du.total)) * 100.0 if du.total > 0 else 0.0
    except Exception:
        used_pct = 0.0
    noise_bytes = _runtime_noise_bytes(root)
    if used_pct >= 92.0 or noise_bytes >= 120 * 1024 * 1024:
        return "aggressive"
    if used_pct >= 84.0 or noise_bytes >= 24 * 1024 * 1024:
        return "balanced"
    return "safe"


def _profile_defaults(profile: str) -> Dict[str, int]:
    if profile == "aggressive":
        return {
            "max_delete": 500,
            "test_tmp_max_age_h": 2,
            "jsonl_max_bytes": 1 * 1024 * 1024,
            "jsonl_keep_lines": 3000,
        }
    if profile == "safe":
        return {
            "max_delete": 80,
            "test_tmp_max_age_h": 48,
            "jsonl_max_bytes": 4 * 1024 * 1024,
            "jsonl_keep_lines": 12000,
        }
    return {
        "max_delete": 200,
        "test_tmp_max_age_h": 12,
        "jsonl_max_bytes": 2 * 1024 * 1024,
        "jsonl_keep_lines": 6000,
    }


def _root_path() -> Path:
    raw = (os.getenv("PROJECT_ROOT") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path.cwd().resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _compact_jsonl_tail(path: Path, *, max_lines: int, max_bytes: int, dry_run: bool) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "trimmed": False}
    try:
        size = path.stat().st_size
    except Exception:
        return {"path": str(path), "exists": True, "trimmed": False, "error": "stat_failed"}
    if size <= max_bytes:
        return {"path": str(path), "exists": True, "trimmed": False, "size_before": size}
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = [ln for ln in f if ln.strip()]
    except Exception:
        return {"path": str(path), "exists": True, "trimmed": False, "error": "read_failed"}
    keep = rows[-max_lines:] if max_lines > 0 else rows
    if dry_run:
        return {
            "path": str(path),
            "exists": True,
            "trimmed": True,
            "size_before": size,
            "lines_before": len(rows),
            "lines_after": len(keep),
            "dry_run": True,
        }
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(keep)
        size_after = path.stat().st_size
        return {
            "path": str(path),
            "exists": True,
            "trimmed": True,
            "size_before": size,
            "size_after": size_after,
            "lines_before": len(rows),
            "lines_after": len(keep),
        }
    except Exception:
        return {"path": str(path), "exists": True, "trimmed": False, "error": "write_failed"}


def _optimize_sqlite(path: Path, *, dry_run: bool) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "optimized": False}
    try:
        size_before = path.stat().st_size
    except Exception:
        return {"path": str(path), "exists": True, "optimized": False, "error": "stat_failed"}
    if dry_run:
        return {"path": str(path), "exists": True, "optimized": True, "dry_run": True, "size_before": size_before}
    try:
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA optimize;")
            conn.execute("ANALYZE;")
            conn.execute("VACUUM;")
        finally:
            conn.close()
        size_after = path.stat().st_size
        return {
            "path": str(path),
            "exists": True,
            "optimized": True,
            "size_before": size_before,
            "size_after": size_after,
        }
    except Exception:
        return {"path": str(path), "exists": True, "optimized": False, "error": "sqlite_opt_failed"}


def run_housekeeping(*, root_path: str | None = None, dry_run: bool = False) -> Dict[str, Any]:
    root = Path(root_path).resolve() if root_path else _root_path()
    profile = _resolve_housekeeping_profile(root)
    defs = _profile_defaults(profile)
    max_delete = _i("HOUSEKEEPING_MAX_DELETE_PER_CYCLE", defs["max_delete"], minimum=10)
    max_age_h = _i("HOUSEKEEPING_TEST_TMP_MAX_AGE_HOURS", defs["test_tmp_max_age_h"], minimum=0)
    cutoff_ts = time.time() - float(max_age_h * 3600)

    removed_files: List[str] = []
    removed_dirs: List[str] = []
    skipped: List[str] = []
    scanned = 0

    def _rm_file(p: Path) -> None:
        nonlocal scanned
        scanned += 1
        if len(removed_files) + len(removed_dirs) >= max_delete:
            return
        if not _is_inside(p, root):
            skipped.append(str(p))
            return
        if dry_run:
            removed_files.append(str(p))
            return
        try:
            p.unlink(missing_ok=True)
            removed_files.append(str(p))
        except Exception:
            skipped.append(str(p))

    def _rm_dir(p: Path) -> None:
        nonlocal scanned
        scanned += 1
        if len(removed_files) + len(removed_dirs) >= max_delete:
            return
        if not _is_inside(p, root):
            skipped.append(str(p))
            return
        if dry_run:
            removed_dirs.append(str(p))
            return
        try:
            shutil.rmtree(p, ignore_errors=False)
            removed_dirs.append(str(p))
        except Exception:
            skipped.append(str(p))

    if _truthy("HOUSEKEEPING_DELETE_PY_CACHE", True):
        for d in root.rglob("__pycache__"):
            _rm_dir(d)
            if len(removed_files) + len(removed_dirs) >= max_delete:
                break
    if len(removed_files) + len(removed_dirs) < max_delete and _truthy("HOUSEKEEPING_DELETE_PYTEST_CACHE", True):
        for d in root.rglob(".pytest_cache"):
            _rm_dir(d)
            if len(removed_files) + len(removed_dirs) >= max_delete:
                break
    if len(removed_files) + len(removed_dirs) < max_delete and _truthy("HOUSEKEEPING_DELETE_PYC_FILES", True):
        for f in root.rglob("*.pyc"):
            _rm_file(f)
            if len(removed_files) + len(removed_dirs) >= max_delete:
                break
    if len(removed_files) + len(removed_dirs) < max_delete and _truthy("HOUSEKEEPING_DELETE_TEST_TMP", True):
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            for f in tests_dir.glob("_tmp_*"):
                try:
                    if f.is_file() and (max_age_h <= 0 or f.stat().st_mtime <= cutoff_ts):
                        _rm_file(f)
                except Exception:
                    skipped.append(str(f))
                if len(removed_files) + len(removed_dirs) >= max_delete:
                    break

    storage: Dict[str, Any] = {"jsonl_compaction": [], "sqlite_optimization": []}
    if _truthy("HOUSEKEEPING_STORAGE_OPTIMIZE_ENABLED", True):
        max_jsonl_bytes = _i("HOUSEKEEPING_JSONL_MAX_BYTES", defs["jsonl_max_bytes"], minimum=1024)
        keep_lines = _i("HOUSEKEEPING_JSONL_KEEP_LINES", defs["jsonl_keep_lines"], minimum=100)
        rt = root / "data" / "runtime"
        jsonl_targets = [
            rt / "llm_usage.jsonl",
            rt / "session_digest.jsonl",
            rt / "route_risk.jsonl",
            rt / "experience_digest.jsonl",
            rt / "strategy_paths.jsonl",
            rt / "runtime_errors.jsonl",
        ]
        for p in jsonl_targets:
            storage["jsonl_compaction"].append(
                _compact_jsonl_tail(p, max_lines=keep_lines, max_bytes=max_jsonl_bytes, dry_run=dry_run)
            )
        sqlite_targets = [
            root / "data" / "database.sqlite",
            rt / "agent_kv.sqlite3",
        ]
        env_db = (os.getenv("DATABASE_PATH") or "").strip()
        if env_db and not env_db.startswith("sqlite://"):
            sqlite_targets.append(Path(env_db))
        env_kv = (os.getenv("AGENT_KV_SQLITE_PATH") or "").strip()
        if env_kv:
            sqlite_targets.append(Path(env_kv))
        seen = set()
        uniq_targets = []
        for p in sqlite_targets:
            rp = str(p.resolve()) if p.is_absolute() else str((root / p).resolve())
            if rp in seen:
                continue
            seen.add(rp)
            uniq_targets.append(Path(rp))
        for p in uniq_targets:
            if _is_inside(p, root) or p.exists():
                storage["sqlite_optimization"].append(_optimize_sqlite(p, dry_run=dry_run))

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "root": str(root),
        "profile": profile,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "removed_total": len(removed_files) + len(removed_dirs),
        "scanned_candidates": scanned,
        "max_delete_per_cycle": max_delete,
        "skipped": skipped[:40],
        "storage_optimization": storage,
    }
