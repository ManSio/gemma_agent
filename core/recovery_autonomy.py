from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.development_passport import (
    passport_file_path,
    rollback_passport_to_latest_backup,
    validate_passport_structure,
)
from core.host_resources import get_host_resource_snapshot
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1


def _runtime_dir() -> Path:
    p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _project_root() -> Path:
    r = os.getenv("PROJECT_ROOT", "").strip()
    return Path(r).resolve() if r else Path.cwd()


def _abs_critical_paths() -> List[Path]:
    raw = os.getenv("AUTONOMY_CRITICAL_PATHS", "").strip()
    root = _project_root()
    if raw:
        paths = []
        for p in raw.split(","):
            s = p.strip()
            if s:
                paths.append((root / s).resolve())
        return paths
    out: List[Path] = []
    for rel in (
        "data/development_passport.json",
        "data/runtime/safe_mode_state.json",
        "data/runtime/autonomy_state.json",
    ):
        out.append((root / rel).resolve())
    rt = (root / "data" / "runtime").resolve()
    if rt.is_dir():
        for f in sorted(rt.glob("*.json")):
            if f.is_file() and f not in out:
                out.append(f)
    return out


def backup_root() -> Path:
    p = Path(os.getenv("AUTONOMY_BACKUP_ROOT", "data/autonomy_backups"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_manifest_rel(rel: str) -> str:
    """Ключи в manifest всегда с / — иначе verify на Linux ломается на старых бэкапах с data\\runtime\\..."""
    s = str(rel).strip().replace("\\", "/")
    while s.startswith("/"):
        s = s[1:]
    return s


def _rel_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


class CriticalDataBackup:
    """Снимки критичных файлов с манифестом SHA-256, ротация, проверка и восстановление."""

    def __init__(self) -> None:
        self._retention = max(1, int(os.getenv("AUTONOMY_BACKUP_RETENTION", "14")))

    @property
    def retention(self) -> int:
        return self._retention

    def list_bundles(self) -> List[Dict[str, Any]]:
        root = backup_root()
        rows: List[Dict[str, Any]] = []
        for d in sorted(root.iterdir(), reverse=True):
            if not d.is_dir() or not d.name.startswith("backup_"):
                continue
            man = d / "manifest.json"
            meta: Dict[str, Any] = {"id": d.name, "path": str(d)}
            if man.is_file():
                try:
                    meta.update(json.loads(man.read_text(encoding="utf-8")))
                except Exception as e:
                    meta["manifest_error"] = str(e)
            vr = self.verify_bundle(d)
            meta["integrity_ok"] = vr.get("ok", False)
            rows.append(meta)
        return rows

    def verify_bundle(self, bundle: Path) -> Dict[str, Any]:
        man_path = bundle / "manifest.json"
        if not man_path.is_file():
            return {"ok": False, "error": "no_manifest"}
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"manifest_read:{e}"}
        files = man.get("files") or {}
        if not isinstance(files, dict):
            return {"ok": False, "error": "bad_files"}
        files_dir = bundle / "files"
        bad: List[str] = []
        for rel, info in files.items():
            if not isinstance(info, dict):
                bad.append(str(rel))
                continue
            expected = info.get("sha256")
            rel_key = _normalize_manifest_rel(str(rel))
            fp = files_dir / rel_key
            if not fp.is_file():
                bad.append(f"missing:{rel}")
                continue
            if expected and _sha256_file(fp) != expected:
                bad.append(f"hash:{rel}")
        return {"ok": len(bad) == 0, "errors": bad} if bad else {"ok": True}

    def create_bundle(self, *, label: str = "manual") -> Dict[str, Any]:
        root = _project_root()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        short = secrets.token_hex(3)
        bid = f"backup_{ts}_{short}"
        bundle = backup_root() / bid
        files_dir = bundle / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        manifest_files: Dict[str, Any] = {}
        copied = 0
        for src in _abs_critical_paths():
            if not src.is_file():
                continue
            rel = _rel_to_root(src, root)
            dest = files_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            manifest_files[rel] = {"sha256": _sha256_file(dest), "size": dest.stat().st_size}
            copied += 1
        man = {
            "version": MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "files": manifest_files,
        }
        (bundle / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        self._rotate()
        MONITOR.inc("autonomy_backup_bundles_total")
        logger.info("Recovery: backup bundle %s (%s files, %s)", bid, copied, label)
        return {"ok": True, "bundle_id": bid, "path": str(bundle), "files": copied}

    def restore_bundle(self, bundle_id: str) -> Dict[str, Any]:
        root = _project_root()
        b = backup_root() / bundle_id
        if not b.is_dir():
            return {"ok": False, "error": "bundle_not_found"}
        vr = self.verify_bundle(b)
        if not vr.get("ok"):
            return {"ok": False, "error": "integrity_failed", "verify": vr}
        man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
        files = man.get("files") or {}
        restored = 0
        for rel in files:
            rel_key = _normalize_manifest_rel(str(rel))
            src = b / "files" / rel_key
            if not src.is_file():
                continue
            dest = root / rel_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored += 1
        MONITOR.inc("autonomy_restore_bundles_total")
        logger.warning("Recovery: restored %s files from %s", restored, bundle_id)
        return {"ok": True, "restored_files": restored, "bundle_id": bundle_id}

    def latest_verified_bundle(self) -> Optional[Path]:
        for row in self.list_bundles():
            if not row.get("integrity_ok"):
                continue
            p = Path(row["path"])
            if p.is_dir():
                return p
        return None

    def _rotate(self) -> None:
        root = backup_root()
        dirs = sorted([d for d in root.iterdir() if d.is_dir() and d.name.startswith("backup_")])
        while len(dirs) > self._retention:
            old = dirs.pop(0)
            try:
                shutil.rmtree(old, ignore_errors=True)
            except Exception as e:
                logger.debug("rotate rmtree %s: %s", old, e)


def _autonomy_state_path() -> Path:
    p = _runtime_dir() / "autonomy_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_autonomy_state() -> Dict[str, Any]:
    p = _autonomy_state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_autonomy_state(data: Dict[str, Any]) -> None:
    _autonomy_state_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_critical_integrity() -> Dict[str, Any]:
    """Проверка читаемости критичных JSON и структуры паспорта."""
    issues: List[str] = []
    root = _project_root()
    pp = Path(passport_file_path())
    if not pp.is_file():
        if not os.getenv("DEVELOPMENT_PASSPORT_JSON", "").strip():
            issues.append("passport_file_missing")
    else:
        try:
            obj = json.loads(pp.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                validate_passport_structure(obj)
        except Exception as e:
            issues.append(f"passport_corrupt:{e}")
    for name in ("safe_mode_state.json", "autonomy_state.json"):
        p = root / "data" / "runtime" / name
        if p.is_file():
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                issues.append(f"runtime_{name}:{e}")
    return {"ok": len(issues) == 0, "issues": issues}


def backup_before_critical_mutations(reason: str = "pre_update") -> Dict[str, Any]:
    """Вызывать перед изменением критичного состояния (паспорт и т.д.)."""
    if os.getenv("AUTONOMY_LAYER_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return {"ok": False, "skipped": True}
    return CriticalDataBackup().create_bundle(label=reason)


def build_unified_health_snapshot(orchestrator: Any) -> Dict[str, Any]:
    """Единый health-snapshot: целостность, резильенс, оценка деградации, бэкапы."""
    from core.connectivity_check import get_external_connectivity_hints_for_health

    rc = getattr(orchestrator, "_resilience", None)
    layer = getattr(orchestrator, "_recovery_autonomy", None)
    integrity = check_critical_integrity()
    ev: Dict[str, Any] = {}
    snap: Dict[str, Any] = {}
    if rc is not None and rc.is_enabled():
        try:
            ev = rc.evaluate(orchestrator)
            snap = rc.snapshot()
        except Exception as e:
            ev = {"error": str(e)}
    backups = CriticalDataBackup().list_bundles()[:20]
    deg = {}
    if isinstance(ev, dict) and "error" not in ev:
        hr_ev = ev.get("host_resources") or {}
        pr = hr_ev.get("pressure") or {}
        deg = {
            "degraded": bool(ev.get("degraded")),
            "critical": bool(ev.get("critical")),
            "kpi_ok": ev.get("kpi_ok"),
            "failed_modules": ev.get("failed_modules"),
            "error_total": ev.get("error_total"),
            "resource_pressure": pr.get("level"),
        }
    return {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "integrity": integrity,
        "resilience": snap,
        "evaluate": ev,
        "degradation_summary": deg,
        "backups_recent": backups,
        "autonomy": layer.snapshot() if layer else {},
        "host_resources": get_host_resource_snapshot(),
        "external_services": get_external_connectivity_hints_for_health(),
    }


class RecoveryAutonomyLayer:
    """
    Recovery & Autonomy: бэкапы с ротацией и проверкой, автoоткат при повреждении,
    периодические снимки, единый health; safe-mode/restart остаются в ResilienceController.
    """

    def __init__(self) -> None:
        self._enabled = os.getenv("AUTONOMY_LAYER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self._backup_every = max(1, int(os.getenv("AUTONOMY_BACKUP_EVERY_N_MAINTENANCE", "1")))
        self._backup = CriticalDataBackup()

    def is_enabled(self) -> bool:
        return self._enabled

    def snapshot(self) -> Dict[str, Any]:
        st = _read_autonomy_state()
        return {
            "enabled": self._enabled,
            "backup_every_n_maintenance": self._backup_every,
            "retention": self._backup.retention,
            "state": st,
            "last_bundles": self._backup.list_bundles()[:5],
        }

    def integrity_report(self) -> Dict[str, Any]:
        return check_critical_integrity()

    def post_boot(self, orchestrator: Any) -> Dict[str, Any]:
        if not self._enabled:
            return {"ok": True, "skipped": True}
        out: Dict[str, Any] = {"integrity": check_critical_integrity()}
        if not out["integrity"]["ok"]:
            out["auto_restore"] = self._auto_restore_from_corruption(out["integrity"]["issues"])
        return out

    def tick(self, orchestrator: Any, *, maintenance_ran: bool) -> Dict[str, Any]:
        if not self._enabled or not maintenance_ran:
            return {"ran": False}
        out: Dict[str, Any] = {"ran": True}
        integrity = check_critical_integrity()
        out["integrity"] = integrity
        if not integrity["ok"]:
            out["auto_restore"] = self._auto_restore_from_corruption(integrity["issues"])
            integrity = check_critical_integrity()
            out["integrity_after"] = integrity

        st = _read_autonomy_state()
        ticks = int(st.get("maintenance_ticks") or 0) + 1
        st["maintenance_ticks"] = ticks
        st["last_maintenance_at"] = datetime.now(timezone.utc).isoformat()
        if ticks % self._backup_every == 0:
            br = self._backup.create_bundle(label="periodic_maintenance")
            out["periodic_backup"] = br
            st["last_backup_at"] = datetime.now(timezone.utc).isoformat()
        _write_autonomy_state(st)
        return out

    def _auto_restore_from_corruption(self, issues: List[str]) -> Dict[str, Any]:
        actions: List[str] = []
        root = _project_root()
        passport_bad = any(
            x == "passport_file_missing" or x.startswith("passport_corrupt") for x in issues
        )
        if passport_bad:
            b = self._backup.latest_verified_bundle()
            if b:
                try:
                    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
                    pf = Path(passport_file_path())
                    if not pf.is_absolute():
                        pf = (root / pf).resolve()
                    rel_pp = _rel_to_root(pf, root)
                    man_files = man.get("files") or {}
                    man_keys = {_normalize_manifest_rel(str(k)) for k in man_files}
                    if rel_pp in man_keys:
                        src = b / "files" / rel_pp
                        if src.is_file():
                            dest = root / rel_pp
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dest)
                            actions.append(f"passport_restored_from_bundle:{b.name}")
                            MONITOR.inc("autonomy_auto_restore_passport_total")
                except Exception as e:
                    actions.append(f"passport_bundle_restore_error:{e}")
            if not check_critical_integrity()["ok"]:
                rb = rollback_passport_to_latest_backup()
                actions.append(f"passport_rollback:{rb}")
                MONITOR.inc("autonomy_auto_restore_passport_total")

        for issue in issues:
            if not issue.startswith("runtime_") or ":" not in issue:
                continue
            head = issue.split(":", 1)[0]
            fname = head[len("runtime_") :]
            if not fname.endswith(".json"):
                continue
            rel = f"data/runtime/{fname}"
            b = self._backup.latest_verified_bundle()
            dest = root / rel
            if b:
                src = b / "files" / rel
                if src.is_file():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    actions.append(f"runtime_restored:{rel}")
                    MONITOR.inc("autonomy_auto_restore_runtime_total")
                    continue
            if fname == "safe_mode_state.json":
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(
                    json.dumps(
                        {"active": False, "reason": "reset_after_corruption"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                actions.append("safe_mode_state_reset")
        return {"actions": actions}

    def manual_backup(self, reason: str = "manual") -> Dict[str, Any]:
        return self._backup.create_bundle(label=reason)

    def manual_restore(self, bundle_id: str) -> Dict[str, Any]:
        return self._backup.restore_bundle(bundle_id)

    def list_backups(self) -> List[Dict[str, Any]]:
        return self._backup.list_bundles()


def resolve_bundle_id(token: str) -> Optional[str]:
    token = (token or "").strip()
    if not token or token.lower() == "latest":
        b = CriticalDataBackup().latest_verified_bundle()
        return b.name if b else None
    if token.startswith("backup_"):
        return token
    # partial match
    for row in CriticalDataBackup().list_bundles():
        if token in row.get("id", ""):
            return row["id"]
    return None
