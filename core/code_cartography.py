"""
Карта исходников проекта: обход .py, отпечатки файлов, журнал снимков, эталон для сравнения.

Пути по умолчанию (от корня репозитория):
  data/runtime/code_ledger.json   — последний полный снимок
  data/runtime/code_history.jsonl — история «что изменилось между прогонами»
  data/runtime/code_baseline.json — эталон (CODE_CARTO_BASELINE_PATH переопределяет)

Переменные окружения:
  CODE_CARTO_ROOT — корень проекта (иначе родитель каталога core/)
  CODE_CARTO_DIRS — через запятую подкаталоги для обхода (по умолчанию core,modules,scripts)
  CODE_CARTO_EXCLUDE_GLOBS — доп. glob через запятую
  CODE_CARTO_FULL_HASH=1 — считать sha256 каждого файла (медленнее, точнее дифф)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

LEDGER_VERSION = 1
_DEFAULT_SCAN_DIRS = ("core", "modules", "scripts")
_EXCLUDE_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".ruff_cache",
    ".cursor",
}


def project_root() -> Path:
    raw = (os.getenv("CODE_CARTO_ROOT") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parent.parent


def _runtime_dir(root: Path) -> Path:
    d = root / "data" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path(root: Optional[Path] = None) -> Path:
    r = root or project_root()
    custom = (os.getenv("CODE_CARTO_LEDGER_PATH") or "").strip()
    if custom:
        return Path(custom).resolve()
    return _runtime_dir(r) / "code_ledger.json"


def history_path(root: Optional[Path] = None) -> Path:
    r = root or project_root()
    custom = (os.getenv("CODE_CARTO_HISTORY_PATH") or "").strip()
    if custom:
        return Path(custom).resolve()
    return _runtime_dir(r) / "code_history.jsonl"


def baseline_path(root: Optional[Path] = None) -> Path:
    r = root or project_root()
    custom = (os.getenv("CODE_CARTO_BASELINE_PATH") or "").strip()
    if custom:
        return Path(custom).resolve()
    return _runtime_dir(r) / "code_baseline.json"


def _scan_dirs(root: Path) -> List[Path]:
    raw = (os.getenv("CODE_CARTO_DIRS") or "").strip()
    names = [x.strip() for x in raw.split(",") if x.strip()] if raw else list(_DEFAULT_SCAN_DIRS)
    out: List[Path] = []
    for n in names:
        p = (root / n).resolve()
        if p.is_dir():
            out.append(p)
    return out


def _extra_exclude_globs() -> Set[str]:
    raw = (os.getenv("CODE_CARTO_EXCLUDE_GLOBS") or "").strip()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _iter_py_files(scan_roots: List[Path], root: Path) -> Iterator[Path]:
    extra = _extra_exclude_globs()
    for base in scan_roots:
        for path in base.rglob("*.py"):
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            parts = set(path.parts)
            if parts & _EXCLUDE_DIR_NAMES:
                continue
            if any(x in str(rel).replace("\\", "/") for x in extra):
                continue
            if "site-packages" in path.parts:
                continue
            yield path


def _fingerprint_file(path: Path) -> Dict[str, Any]:
    st = path.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    entry: Dict[str, Any] = {"mtime_utc": mtime, "size": int(st.st_size)}
    if os.getenv("CODE_CARTO_FULL_HASH", "").strip().lower() in {"1", "true", "yes", "on"}:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        entry["sha256"] = h.hexdigest()
    return entry


def scan_python_sources(root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Относительные POSIX-пути → {mtime_utc, size, sha256?}."""
    r = root or project_root()
    roots = _scan_dirs(r)
    out: Dict[str, Dict[str, Any]] = {}
    for p in _iter_py_files(roots, r):
        key = p.relative_to(r).as_posix()
        try:
            out[key] = _fingerprint_file(p)
        except OSError as e:
            out[key] = {"error": str(e)}
    return dict(sorted(out.items()))


def git_head_info(root: Optional[Path] = None) -> Dict[str, Any]:
    r = root or project_root()
    if not (r / ".git").exists():
        return {"available": False}
    try:
        br = subprocess.run(
            ["git", "-C", str(r), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = subprocess.run(
            ["git", "-C", str(r), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dt = subprocess.run(
            ["git", "-C", str(r), "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        subj = subprocess.run(
            ["git", "-C", str(r), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "available": True,
            "branch": (br.stdout or "").strip() or None,
            "short_sha": (sha.stdout or "").strip() or None,
            "last_commit_iso": (dt.stdout or "").strip() or None,
            "last_subject": (subj.stdout or "").strip() or None,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return {"available": False, "error": str(e)}


def _diff_maps(
    old: Dict[str, Dict[str, Any]],
    new: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], List[str], List[str]]:
    old_k, new_k = set(old), set(new)
    added = sorted(new_k - old_k)
    removed = sorted(old_k - new_k)
    modified: List[str] = []
    for k in sorted(old_k & new_k):
        a, b = old[k], new[k]
        if "error" in a or "error" in b:
            modified.append(k)
            continue
        if a.get("size") != b.get("size") or a.get("mtime_utc") != b.get("mtime_utc"):
            modified.append(k)
            continue
        ha, hb = a.get("sha256"), b.get("sha256")
        if ha is not None and hb is not None and ha != hb:
            modified.append(k)
    return added, removed, modified


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("code_cartography: не прочитан %s: %s", path, e)
        return None


def _append_history(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def tail_history(path: Path, limit: int = 12) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def compare_to_baseline(
    current_files: Dict[str, Dict[str, Any]],
    baseline_path: Path,
) -> Dict[str, Any]:
    base = _load_json(baseline_path)
    if not base or not isinstance(base.get("files"), dict):
        return {"baseline_present": False, "path": str(baseline_path)}
    bf = base["files"]
    if not isinstance(bf, dict):
        return {"baseline_present": False, "path": str(baseline_path)}
    added, removed, modified = _diff_maps(bf, current_files)
    return {
        "baseline_present": True,
        "path": str(baseline_path),
        "baseline_saved_utc": base.get("generated_utc"),
        "drift_added": added,
        "drift_removed": removed,
        "drift_modified": modified,
        "drift_total": len(added) + len(removed) + len(modified),
    }


def save_baseline(
    current_files: Dict[str, Dict[str, Any]],
    root: Optional[Path] = None,
    dest: Optional[Path] = None,
) -> Dict[str, Any]:
    r = root or project_root()
    path = dest or baseline_path(r)
    payload = {
        "version": LEDGER_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(r),
        "git": git_head_info(r),
        "files": current_files,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path), "file_count": len(current_files)}


@dataclass
class ScanRecordResult:
    snapshot: Dict[str, Any]
    ledger_written: bool
    files: Dict[str, Dict[str, Any]]


def scan_and_maybe_record(
    *,
    persist: bool = False,
    root: Optional[Path] = None,
) -> ScanRecordResult:
    """
    Сканирует .py под проектом. При persist=True сравнивает с прошлым ledger,
    пишет code_history.jsonl и обновляет code_ledger.json.
    """
    r = root or project_root()
    files = scan_python_sources(r)
    git = git_head_info(r)
    prev_obj = _load_json(ledger_path(r))
    prev_files = prev_obj.get("files") if isinstance(prev_obj, dict) else None
    if not isinstance(prev_files, dict):
        prev_files = {}

    added, removed, modified = _diff_maps(prev_files, files) if prev_files else ([], [], [])
    drift = compare_to_baseline(files, baseline_path(r))

    snapshot: Dict[str, Any] = {
        "ledger_version": LEDGER_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(r),
        "scan_dirs": [p.relative_to(r).as_posix() for p in _scan_dirs(r)],
        "file_count": len(files),
        "git": git,
        "since_last_ledger": {
            "added": added,
            "removed": removed,
            "modified": modified,
            "has_previous_ledger": bool(prev_files),
        },
        "baseline_drift": drift,
        "history_tail": tail_history(history_path(r), 10),
    }

    if persist:
        write_errs: List[str] = []
        if prev_files or added or removed or modified:
            try:
                _append_history(
                    history_path(r),
                    {
                        "ts": snapshot["generated_utc"],
                        "event": "scan",
                        "file_count": len(files),
                        "added": added,
                        "removed": removed,
                        "modified": modified[:200],
                        "modified_count": len(modified),
                        "git_short": (git or {}).get("short_sha"),
                    },
                )
            except OSError as e:
                logger.warning("code_cartography: history append %s: %s", history_path(r), e)
                write_errs.append(f"history: {e}")
        ledger_obj = {
            "version": LEDGER_VERSION,
            "generated_utc": snapshot["generated_utc"],
            "root": str(r),
            "git": git,
            "files": files,
        }
        lp = ledger_path(r)
        try:
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(json.dumps(ledger_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("code_cartography: ledger write %s: %s", lp, e)
            write_errs.append(f"ledger: {e}")
            snapshot["ledger_write_error"] = "; ".join(write_errs)
            snapshot["history_tail"] = tail_history(history_path(r), 10)
            return ScanRecordResult(snapshot=snapshot, ledger_written=False, files=files)
        snapshot["history_tail"] = tail_history(history_path(r), 10)
        if write_errs:
            snapshot["ledger_write_error"] = "; ".join(write_errs)
        return ScanRecordResult(snapshot=snapshot, ledger_written=True, files=files)

    return ScanRecordResult(snapshot=snapshot, ledger_written=False, files=files)


def build_bundle_slice(*, persist: bool = False) -> Dict[str, Any]:
    """Компактный блок для diagnostic ZIP (без полного списка файлов)."""
    res = scan_and_maybe_record(persist=persist)
    snap = res.snapshot
    files = res.files
    by_mtime: List[Tuple[str, str]] = []
    for rel, meta in files.items():
        if isinstance(meta, dict) and "mtime_utc" in meta:
            by_mtime.append((meta["mtime_utc"], rel))
    by_mtime.sort(reverse=True)
    recent = [rel for _, rel in by_mtime[:40]]

    tree_counts: Dict[str, int] = {}
    for rel in files:
        top = rel.split("/")[0] if "/" in rel else rel.split("\\")[0]
        tree_counts[top] = tree_counts.get(top, 0) + 1

    out = {
        "generated_utc": snap.get("generated_utc"),
        "root": snap.get("root"),
        "file_count": snap.get("file_count"),
        "scan_dirs": snap.get("scan_dirs"),
        "git": snap.get("git"),
        "since_last_ledger": snap.get("since_last_ledger"),
        "baseline_drift": {
            k: v
            for k, v in (snap.get("baseline_drift") or {}).items()
            if k not in {"drift_added", "drift_removed", "drift_modified"}
        },
        "baseline_drift_counts": {
            "added": len((snap.get("baseline_drift") or {}).get("drift_added") or []),
            "removed": len((snap.get("baseline_drift") or {}).get("drift_removed") or []),
            "modified": len((snap.get("baseline_drift") or {}).get("drift_modified") or []),
        },
        "files_by_top_dir": dict(sorted(tree_counts.items())),
        "recently_modified_paths": recent,
        "history_tail": snap.get("history_tail"),
        "ledger_updated_this_run": res.ledger_written,
    }
    # Короткие списки дрифта (пути), не весь проект
    bd = snap.get("baseline_drift") or {}
    if isinstance(bd, dict) and bd.get("baseline_present"):
        for key, lim in (("drift_added", 30), ("drift_removed", 30), ("drift_modified", 40)):
            xs = bd.get(key) or []
            if isinstance(xs, list):
                out.setdefault("baseline_drift_samples", {})[key] = xs[:lim]
    sl = snap.get("since_last_ledger") or {}
    if isinstance(sl, dict) and sl.get("has_previous_ledger"):
        out["since_last_ledger_samples"] = {
            "added": (sl.get("added") or [])[:25],
            "removed": (sl.get("removed") or [])[:25],
            "modified": (sl.get("modified") or [])[:40],
        }
    return out


def format_code_map_html(snapshot: Dict[str, Any]) -> str:
    """Краткий HTML для Telegram."""
    from core.telegram_ui import esc

    out: list[str] = ["🗺️ <b>Карта кода</b>", ""]
    head_inner = [
        f"Файлов .py: <b>{esc(snapshot.get('file_count'))}</b>",
        f"Корень: <code>{esc(snapshot.get('root'))}</code>",
    ]
    lwe = snapshot.get("ledger_write_error")
    if lwe:
        head_inner.append(f"⚠️ Запись на диск: <code>{esc(str(lwe))}</code>")
    git = snapshot.get("git") if isinstance(snapshot.get("git"), dict) else {}
    if git.get("available"):
        head_inner.append(
            f"<b>Git:</b> <code>{esc(git.get('branch'))}</code> @ <code>{esc(git.get('short_sha'))}</code>"
        )
        if git.get("last_subject"):
            head_inner.append(f"<i>{esc(git.get('last_subject'))}</i>")
    else:
        head_inner.append("<i>Git недоступен или не репозиторий</i>")
    out.extend(["<blockquote>", *head_inner, "</blockquote>", ""])

    sl = snapshot.get("since_last_ledger") if isinstance(snapshot.get("since_last_ledger"), dict) else {}
    sl_lines: list[str]
    if sl.get("has_previous_ledger"):
        sl_lines = [
            f"• +добавлено: <b>{esc(len(sl.get('added') or []))}</b> · "
            f"−удалено: <b>{esc(len(sl.get('removed') or []))}</b> · "
            f"изменено: <b>{esc(len(sl.get('modified') or []))}</b>"
        ]
        for label, key in (("+", "added"), ("−", "removed"), ("~", "modified")):
            xs = sl.get(key) or []
            if isinstance(xs, list) and xs:
                preview = ", ".join(esc(x) for x in xs[:8])
                more = f" … +{len(xs) - 8}" if len(xs) > 8 else ""
                sl_lines.append(f"• {label} <code>{preview}{more}</code>")
    else:
        sl_lines = [
            "<i>Первый снимок или нет <code>data/runtime/code_ledger.json</code> — сохранён новый ledger.</i>"
        ]
    out.extend(["📂 <b>С прошлого снимка ledger</b>", "", "<blockquote>", *sl_lines, "</blockquote>", ""])

    bd = snapshot.get("baseline_drift") if isinstance(snapshot.get("baseline_drift"), dict) else {}
    bd_lines: list[str]
    if bd.get("baseline_present"):
        bd_lines = [
            f"Снят: <code>{esc(bd.get('baseline_saved_utc'))}</code>",
            f"Отличия: +{esc(len(bd.get('drift_added') or []))} "
            f"−{esc(len(bd.get('drift_removed') or []))} "
            f"~{esc(len(bd.get('drift_modified') or []))}",
        ]
        for label, key in (("+", "drift_added"), ("−", "drift_removed"), ("~", "drift_modified")):
            xs = bd.get(key) or []
            if isinstance(xs, list) and xs:
                preview = ", ".join(esc(x) for x in xs[:6])
                more = f" … +{len(xs) - 6}" if len(xs) > 6 else ""
                bd_lines.append(f"• {label} <code>{preview}{more}</code>")
    else:
        bd_lines = [
            "<i>Эталон не задан. Записать: <code>/admin_code_baseline_set</code> "
            "(после рефакторинга — сравнивать дрифт).</i>"
        ]
    out.extend(["📐 <b>Эталон (baseline)</b>", "", "<blockquote>", *bd_lines, "</blockquote>", ""])

    hist = snapshot.get("history_tail") if isinstance(snapshot.get("history_tail"), list) else []
    if hist:
        h_lines: list[str] = []
        for row in hist[-5:]:
            if not isinstance(row, dict):
                continue
            h_lines.append(
                f"• <code>{esc(row.get('ts', '')[:19])}</code> "
                f"+{esc(len(row.get('added') or []))}/"
                f"−{esc(len(row.get('removed') or []))}/"
                f"~{esc(row.get('modified_count', len(row.get('modified') or [])))}"
            )
        out.extend(["🕐 <b>История снимков (хвост)</b>", "", "<blockquote>", *h_lines, "</blockquote>", ""])

    out.append(
        "<blockquote><i>Полный JSON: <code>/admin_code_map_json</code> · в ZIP: <code>code_cartography</code></i></blockquote>"
    )
    return "\n".join(out)
