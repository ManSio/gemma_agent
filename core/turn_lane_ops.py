"""3-lane ops surface: DIALOGUE / FACT / DEEP для admin, footer и метрик."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.turn_contract import LANE_DEEP, LANE_DIALOGUE, LANE_FACT, lane_from_profile

_LANE_LABELS_RU: Dict[str, str] = {
    LANE_DIALOGUE: "диалог",
    LANE_FACT: "факт",
    LANE_DEEP: "глубокий",
}


def normalize_lane(lane: str) -> str:
    """Нормализовать lane к DIALOGUE | FACT | DEEP."""
    token = (lane or "").strip().upper()
    if token in (LANE_DIALOGUE, LANE_FACT, LANE_DEEP):
        return token
    return LANE_DIALOGUE


def lane_label_ru(lane: str) -> str:
    """Человекочитаемая подпись lane."""
    return _LANE_LABELS_RU.get(normalize_lane(lane), (lane or "диалог"))


def lane_from_turn_row(row: Mapping[str, Any]) -> str:
    """Извлечь lane из строки turns.jsonl или вывести из profile/shortcut."""
    raw = str(row.get("lane") or "").strip()
    if raw:
        return normalize_lane(raw)
    profile = str(row.get("profile") or row.get("kv_profile") or "").strip()
    sc = str(row.get("short_circuit") or row.get("planner_bypass") or "").strip()
    return lane_from_profile(profile, short_circuit=sc)


def summarize_lane_distribution(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка lane за окно turns.jsonl."""
    counts = {LANE_DIALOGUE: 0, LANE_FACT: 0, LANE_DEEP: 0}
    hops = 0
    drift = 0
    prev_lane = ""
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("type") in ("scenario", "pre_send"):
            continue
        total += 1
        ln = lane_from_turn_row(row)
        counts[ln] = counts.get(ln, 0) + 1
        if prev_lane and ln != prev_lane:
            hops += 1
        prev_lane = ln
        if row.get("turn_hash_drift"):
            drift += 1
    return {
        "total": total,
        "counts": counts,
        "lane_hops": hops,
        "turn_hash_drift": drift,
    }


def format_lane_summary_short(summary: Mapping[str, Any]) -> str:
    """Компактная строка D/F/Deep для admin_self."""
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return (
        f"D={int(counts.get(LANE_DIALOGUE, 0))} "
        f"F={int(counts.get(LANE_FACT, 0))} "
        f"Deep={int(counts.get(LANE_DEEP, 0))}"
    )


def lane_from_meta(
    *,
    output_meta: Optional[Mapping[str, Any]] = None,
    route_context: Optional[Mapping[str, Any]] = None,
) -> str:
    """Lane для footer из turn_contract в meta/context."""
    for src in (output_meta, route_context):
        if not isinstance(src, Mapping):
            continue
        tc = src.get("turn_contract")
        if isinstance(tc, Mapping) and tc.get("lane"):
            return normalize_lane(str(tc.get("lane")))
        if src.get("sticky_lane"):
            return normalize_lane(str(src.get("sticky_lane")))
        if src.get("lane"):
            return normalize_lane(str(src.get("lane")))
    profile = ""
    sc = ""
    if isinstance(output_meta, Mapping):
        profile = str(output_meta.get("brain_profile") or output_meta.get("router_profile") or "")
        sc = str(output_meta.get("planner_bypass") or output_meta.get("short_circuit") or "")
    return lane_from_profile(profile, short_circuit=sc)
