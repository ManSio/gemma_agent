"""Кластеризация route_risk (без ML): fingerprint + error_type + intent."""
from __future__ import annotations

import logging

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

def _path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "data" / "runtime" / "route_risk.jsonl"


def record_ts_epoch(rec: Dict[str, Any]) -> float:
    """Unix epoch из ts (ISO string или float) — для окон route_risk."""
    raw = rec.get("ts")
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return 0.0
    s = raw.strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _cluster_key(rec: Dict[str, Any]) -> str:
    et = str(rec.get("error_type") or "unknown").strip().lower()
    intent = str(rec.get("intent") or "unknown").strip().lower()
    fp = str(rec.get("fp") or rec.get("fingerprint") or "").strip()[:16]
    mod = str(rec.get("module") or "").strip().lower()[:32]
    sk = str(rec.get("skill") or "").strip().lower()[:24]
    return f"{et}|{intent}|{mod}|{fp}|{sk}"


def cluster_route_risk_recent(
    *,
    hours: float = 6.0,
    min_count: int = 2,
    limit: int = 20,
) -> Dict[str, Any]:
    """Группировка stumble за окно; возвращает clusters отсортированные по count."""
    path = _path()
    if not path.is_file():
        return {"clusters": [], "total_stumbles": 0, "window_hours": hours}
    cutoff = time.time() - max(0.5, hours) * 3600
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    total = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            t = record_ts_epoch(rec)
            if t <= 0 or t < cutoff:
                continue
            total += 1
            buckets[_cluster_key(rec)].append(rec)
    except OSError:
        return {"clusters": [], "total_stumbles": 0, "window_hours": hours}

    clusters: List[Dict[str, Any]] = []
    for key, items in buckets.items():
        if len(items) < min_count:
            continue
        sample = items[-1]
        clusters.append(
            {
                "cluster_key": key,
                "count": len(items),
                "error_type": sample.get("error_type"),
                "intent": sample.get("intent"),
                "module": sample.get("module"),
                "skill": sample.get("skill"),
                "sample_detail": str(sample.get("detail") or "")[:120],
            }
        )
    clusters.sort(key=lambda c: int(c.get("count") or 0), reverse=True)
    return {
        "window_hours": hours,
        "total_stumbles": total,
        "clusters": clusters[:limit],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def maybe_auto_lesson_from_clusters(*, hours: float = 1.0, min_count: int = 3) -> int:
    """
    Если кластер ≥ min_count за час — создать reflexion-урок (без LLM).
    Вкл: ROUTE_RISK_CLUSTER_AUTO_LESSON=true
  """
    raw = (os.getenv("ROUTE_RISK_CLUSTER_AUTO_LESSON") or "").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return 0
    try:
        min_count = max(2, int(os.getenv("ROUTE_RISK_CLUSTER_AUTO_LESSON_MIN", str(min_count))))
    except ValueError:
        pass
    pack = cluster_route_risk_recent(hours=hours, min_count=min_count, limit=8)
    created = 0
    try:
        from core.self_learning.lesson_manager import LessonManager
        from core.self_learning.models import Lesson

        lm = LessonManager.get_instance()
        for cl in pack.get("clusters") or []:
            if not isinstance(cl, dict):
                continue
            cnt = int(cl.get("count") or 0)
            if cnt < min_count:
                continue
            lesson_id = f"rr_cluster_{abs(hash(str(cl.get('cluster_key', '')))) % 10_000_000}"
            if lm.get_lesson_by_id(lesson_id):
                continue
            text = (
                f"Повторяющаяся ошибка маршрута ({cnt}× за {hours}ч): "
                f"{cl.get('error_type')} intent={cl.get('intent')} module={cl.get('module')}. "
                f"Пример: {(cl.get('sample_detail') or '')[:80]}"
            )
            lesson = Lesson.new(
                content=text[:500],
                source="route_risk_cluster",
                source_context={"cluster": cl},
                category="route_risk_cluster",
                tags=["auto", "route_risk"],
            )
            lesson.id = lesson_id
            lesson.effectiveness_score = 0.55
            lm._append_jsonl(lesson)
            created += 1
    except Exception as e:
        logger.debug('%s optional failed: %s', 'route_risk_cluster', e, exc_info=True)
    return created
