"""Детекция стагнации самообучения: средний v_c по скиллам во времени."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _snapshot_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "learning_confidence_snapshots.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _avg_skill_vc(user_id: Optional[str] = None) -> Optional[float]:
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, iter_prefix

        if not agent_kv_enabled():
            return None
        prefix = f"{user_id}|" if user_id else ""
        rows = list(iter_prefix("reputation_skill", prefix, branch=agent_kv_branch()))
        if not rows:
            return None
        vals: List[float] = []
        for _, v in rows:
            if isinstance(v, dict) and v.get("v_c") is not None:
                try:
                    vals.append(float(v["v_c"]))
                except (TypeError, ValueError):
                    pass
        return sum(vals) / len(vals) if vals else None
    except Exception:
        return None


def record_confidence_snapshot(user_id: Optional[str] = None) -> Dict[str, Any]:
    avg = _avg_skill_vc(user_id)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "avg_skill_v_c": round(avg, 4) if avg is not None else None,
        "skills_tracked": True,
    }
    try:
        with open(_snapshot_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("stagnation snapshot: %s", e)
    return row


def detect_stagnation(*, days: float = 7.0, min_delta: float = 0.03) -> Dict[str, Any]:
    """
    Сравнить средний v_c сейчас с снимком ~days назад.
    stagnation=True если рост < min_delta.
    """
    path = _snapshot_path()
    if not path.is_file():
        return {"stagnation": False, "reason": "no_history"}
    cutoff = time.time() - days * 86400
    old_vals: List[float] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00")).timestamp()
                if ts <= cutoff + 3600:
                    v = rec.get("avg_skill_v_c")
                    if v is not None:
                        old_vals.append(float(v))
            except Exception:
                continue
    except OSError:
        return {"stagnation": False, "reason": "read_error"}
    if not old_vals:
        return {"stagnation": False, "reason": "insufficient_old_samples"}
    old_avg = sum(old_vals) / len(old_vals)
    now_avg = _avg_skill_vc()
    if now_avg is None:
        return {"stagnation": False, "reason": "no_current_skills"}
    delta = now_avg - old_avg
    stagnant = delta < min_delta
    return {
        "stagnation": stagnant,
        "old_avg_v_c": round(old_avg, 4),
        "now_avg_v_c": round(now_avg, 4),
        "delta": round(delta, 4),
        "window_days": days,
        "recommendation": "raise ROUTE_RISK_CLUSTER_AUTO_LESSON or review weak_skills in /admin_reputation"
        if stagnant
        else "ok",
    }
