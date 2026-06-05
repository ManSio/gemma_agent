"""Периодическое самообучение: кластеры, experience rules, снимки стагнации."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _state_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "data" / "runtime" / "learning_maintenance_state.json"


def _interval_sec() -> int:
    try:
        return max(3600, int(os.getenv("LEARNING_MAINTENANCE_INTERVAL_SEC", "21600")))
    except ValueError:
        return 21600


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def maybe_run_learning_maintenance(*, force: bool = False) -> Dict[str, Any]:
    """
    Вызывается из autopilot inner tick.
    Раз в LEARNING_MAINTENANCE_INTERVAL_SEC (default 6h).
    """
    if os.getenv("LEARNING_MAINTENANCE_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return {"skipped": True, "reason": "disabled"}
    now = time.time()
    st = _load_state()
    last = float(st.get("last_run_ts") or 0)
    if not force and last and (now - last) < _interval_sec():
        return {"skipped": True, "next_in_sec": int(_interval_sec() - (now - last))}

    report: Dict[str, Any] = {"ts": now, "steps": {}}

    try:
        from core.route_risk_cluster import cluster_route_risk_recent, maybe_auto_lesson_from_clusters

        report["steps"]["clusters"] = cluster_route_risk_recent(hours=6.0, min_count=2)
        report["steps"]["auto_lessons"] = maybe_auto_lesson_from_clusters(hours=1.0)
    except Exception as e:
        report["steps"]["clusters_error"] = str(e)[:120]

    try:
        from core.experience_rules import run_experience_rules_cycle

        report["steps"]["experience_rules"] = run_experience_rules_cycle(hours=24.0)
    except Exception as e:
        report["steps"]["experience_rules_error"] = str(e)[:120]

    try:
        from core.learning_stagnation import detect_stagnation, record_confidence_snapshot

        record_confidence_snapshot()
        report["steps"]["stagnation"] = detect_stagnation()
    except Exception as e:
        report["steps"]["stagnation_error"] = str(e)[:120]

    st["last_run_ts"] = now
    st["last_report"] = report
    _save_state(st)
    logger.info("[learning_maintenance] completed: %s", list(report.get("steps", {}).keys()))
    return report


def format_learning_maintenance_html(report: Dict[str, Any]) -> str:
    from core.telegram_ui import esc, report_pre_kv

    if report.get("skipped"):
        reason = str(report.get("reason") or report.get("next_in_sec") or "skipped")
        return f"🔧 <b>Learning maintenance</b>\n\n<blockquote>⏭ Пропуск: {esc(reason)}</blockquote>"

    steps = report.get("steps") if isinstance(report.get("steps"), dict) else {}
    rows: list[tuple[str, str]] = []
    for name, val in steps.items():
        if name.endswith("_error"):
            rows.append((name, str(val)[:60]))
        elif name == "clusters" and isinstance(val, dict):
            rows.append(("clusters", f"{len(val.get('clusters') or [])} класт., stumbles={val.get('total_stumbles', 0)}"))
        elif name == "auto_lessons":
            rows.append(("auto_lessons", str(val)))
        elif isinstance(val, dict):
            rows.append((name, str(list(val.keys())[:4])[:50]))
        else:
            rows.append((name, str(val)[:50]))

    return "\n".join(
        [
            "🔧 <b>Learning maintenance</b>",
            "",
            "<blockquote>",
            report_pre_kv(rows or [("steps", "(пусто)")], label_max=18),
            "</blockquote>",
            "",
            "<blockquote><i>JSON: <code>/admin_run_learning_json</code></i></blockquote>",
        ]
    )
