"""Regression runner для structural replay TurnContract (Phase 3.3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.short_circuit_registry import lookup_short_circuit
from core.turn_contract import lane_from_profile
from core.turn_lane_ops import normalize_lane
from core.turn_meaning import resolve_turn_meaning_structural


def default_regression_fixture_path() -> Path:
    """Путь к bundled fixture с 20 кейсами."""
    return Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "turn_regression_cases.json"


def load_regression_cases(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Загрузить regression cases из JSON."""
    if path is not None:
        p = path
    else:
        p = default_regression_fixture_path()
        prod = p.parent / "turn_regression_prod.json"
        if prod.is_file():
            p = prod
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def replay_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Structural replay одного regression case."""
    user_text = str(case.get("user_text") or "")
    recent = case.get("recent_dialogue") or case.get("recent_before") or []
    ctx: Dict[str, Any] = {"recent_dialogue": recent if isinstance(recent, list) else []}
    if case.get("turn_meaning"):
        ctx["turn_meaning"] = case.get("turn_meaning")
    if case.get("discourse_resolution"):
        ctx["discourse_resolution"] = case.get("discourse_resolution")
    if case.get("dialogue_state"):
        ctx["dialogue_state"] = case.get("dialogue_state")
    meaning = resolve_turn_meaning_structural(user_text, ctx)
    sc = str(case.get("short_circuit") or case.get("planner_bypass") or "").strip()
    profile = str(case.get("profile") or "").strip()
    ent = lookup_short_circuit(sc) if sc else None
    lane = normalize_lane(
        str((ent or {}).get("lane") or lane_from_profile(profile, short_circuit=sc))
    )
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    got = {
        "referent": meaning.referent,
        "thread_action": meaning.thread_action,
        "lane": lane,
        "short_circuit": sc or None,
    }
    mismatches: List[str] = []
    for key, exp in expect.items():
        if str(got.get(key) or "") != str(exp):
            mismatches.append(f"{key}: got={got.get(key)!r} expect={exp!r}")
    return {
        "id": str(case.get("id") or "")[:48],
        "user_text_head": user_text[:80],
        "got": got,
        "expect": expect,
        "ok": not mismatches,
        "mismatches": mismatches,
    }


def run_regression_suite(cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Прогнать все cases; вернуть сводку."""
    rows = cases if cases is not None else load_regression_cases()
    results = [replay_case(c) for c in rows]
    failed = [r for r in results if not r.get("ok")]
    return {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }
