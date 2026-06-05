"""Слабое обучение: обобщённые правила из успешных записей experience_digest."""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _experience_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    custom = (os.getenv("GEMMA_EXPERIENCE_PATH") or "").strip()
    if custom:
        return Path(custom)
    return Path(root) / "data" / "runtime" / "experience_digest.jsonl"


def experience_rules_enabled() -> bool:
    raw = os.getenv("EXPERIENCE_RULES_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _min_ok_per_rule() -> int:
    try:
        return max(2, int(os.getenv("EXPERIENCE_RULES_MIN_OK", "3")))
    except ValueError:
        return 3


def extract_rules_from_experience(*, hours: float = 24.0) -> List[Dict[str, Any]]:
    """
    Группировка ok-записей: intent → лучший module/skill по частоте.
    Без LLM — только статистика.
    """
    path = _experience_path()
    if not path.is_file():
        return []
    cutoff = time.time() - hours * 3600
    by_intent: Dict[str, Counter] = defaultdict(Counter)
    skill_by_intent: Dict[str, Counter] = defaultdict(Counter)
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(rec.get("outcome") or "") != "ok":
                continue
            try:
                ts = rec.get("ts")
                if isinstance(ts, str):
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                else:
                    t = float(ts or 0)
            except Exception:
                t = 0
            if t and t < cutoff:
                continue
            intent = str(rec.get("intent") or "unknown")
            mod = str(rec.get("module") or "")
            sk = str(rec.get("skill") or "")
            if mod:
                by_intent[intent][mod] += 1
            if sk:
                skill_by_intent[intent][sk] += 1
    except OSError as e:
        logger.debug("experience_rules read: %s", e)
        return []

    min_ok = _min_ok_per_rule()
    rules: List[Dict[str, Any]] = []
    for intent, ctr in by_intent.items():
        if not ctr:
            continue
        mod, count = ctr.most_common(1)[0]
        if count < min_ok:
            continue
        sk_ctr = skill_by_intent.get(intent)
        sk = sk_ctr.most_common(1)[0][0] if sk_ctr else ""
        instruction = (
            f"Для intent={intent} чаще всего успешен модуль {mod}"
            + (f" и skill {sk}" if sk else "")
            + f" ({count} ok за {hours:.0f}ч). Предпочитай этот маршрут при похожих запросах."
        )
        rules.append(
            {
                "intent": intent,
                "module": mod,
                "skill": sk or None,
                "ok_count": count,
                "instruction": instruction,
            }
        )
    return rules


def apply_rules_to_ephemeral(rules: List[Dict[str, Any]]) -> int:
    """Записать правила как ephemeral lessons (триггер = intent keyword)."""
    if not experience_rules_enabled() or not rules:
        return 0
    try:
        from core.ephemeral_lessons import add_lesson
    except Exception:
        return 0
    created = 0
    for r in rules:
        intent = str(r.get("intent") or "")
        inst = str(r.get("instruction") or "").strip()
        if not intent or not inst:
            continue
        trig = intent if len(intent) >= 3 else f"intent_{intent}"
        try:
            add_lesson(trig, inst, meta={"source": "experience_rules", "auto": True})
            created += 1
        except Exception as e:
            logger.debug("experience_rules add_lesson: %s", e)
    return created


def run_experience_rules_cycle(*, hours: float = 24.0) -> Dict[str, Any]:
    rules = extract_rules_from_experience(hours=hours)
    n = apply_rules_to_ephemeral(rules)
    return {
        "rules_found": len(rules),
        "ephemeral_written": n,
        "rules": rules[:10],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
