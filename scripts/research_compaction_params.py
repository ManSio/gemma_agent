"""Parameter sweep: memory max, thresholds, turn triggers — read-only research."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.brain.prompt_pack import estimate_tokens_approx
from core.context_compression import compress_recent_dialogue, normalize_dialogue_message_rows, trim_dialogue_messages_paired


def count_turns(msgs: List[Dict[str, Any]]) -> int:
    n = 0
    for i, m in enumerate(msgs):
        role = str(m.get("role") or "").lower()
        if role == "user":
            n += 1
        elif role == "assistant":
            is_last = i == len(msgs) - 1
            nxt = i + 1 < len(msgs) and str(msgs[i + 1].get("role") or "").lower() == "user"
            if is_last or nxt:
                n += 1
    return n


def simulate_store(
    n_turns: int,
    *,
    memory_max: int,
    user_chars: int = 80,
    assistant_chars: int = 400,
    long_every: Optional[int] = None,
    long_chars: int = 6000,
) -> Tuple[List[Dict], int, int]:
    msgs: List[Dict] = []
    for i in range(n_turns):
        u = f"u{i}:" + "x" * max(1, user_chars - 4)
        alen = long_chars if long_every and (i + 1) % long_every == 0 else assistant_chars
        a = f"b{i}:" + "y" * max(1, alen - 4)
        msgs.extend([{"role": "user", "text": u}, {"role": "assistant", "text": a}])
    pre = normalize_dialogue_message_rows(msgs)
    trimmed = trim_dialogue_messages_paired(pre, memory_max)
    stored = compress_recent_dialogue(trimmed)
    overflow = max(0, len(pre) - len(trimmed))
    dlg_tok = estimate_tokens_approx("\n".join(str(m.get("text") or "") for m in stored))
    return stored, overflow, dlg_tok


def would_compact(
    *,
    prompt_overhead: int,
    dlg_tokens: int,
    stored_msgs: int,
    turn_index: int,
    budget: int,
    threshold: float,
    turn_limit: int,
    min_msgs: int,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    est = prompt_overhead + dlg_tokens
    lim = int(budget * threshold)
    if est > lim:
        reasons.append(f"prompt_est={est}>{lim}")
    if turn_index > turn_limit:
        reasons.append(f"turn_index={turn_index}>{turn_limit}")
    if stored_msgs >= min_msgs and dlg_tokens > int(budget * 0.5):
        reasons.append(f"dialogue_half_budget={dlg_tokens}>{int(budget*0.5)}")
    return bool(reasons), reasons


@dataclass
class GridRow:
    memory_max: int
    budget: int
    threshold: float
    turn_limit: int
    scenario: str
    n_turns: int
    stored_msgs: int
    overflow_msgs: int
    dialogue_tokens: int
    prompt_est: int
    compact_fires: bool
    reasons: List[str]
    constraint_kept_in_store: bool


def main() -> None:
    overhead = 3500
    scenarios = [
        ("chitchat_15", 15, 80, 400, None, 0),
        ("chitchat_30", 30, 80, 400, None, 0),
        ("heavy_12", 12, 80, 1200, None, 0),
        ("paste_10", 10, 80, 400, 4, 8000),
        ("marker_test_20", 20, 60, 200, None, 0),
    ]
    memory_opts = [10, 12, 14]
    budget = 12000
    threshold_opts = [0.70, 0.75, 0.80]
    turn_limit_opts = [6, 8, 10]

    rows: List[GridRow] = []
    for mem in memory_opts:
        for thr in threshold_opts:
            for tl in turn_limit_opts:
                for sc_name, n, uc, ac, le, lc in scenarios:
                    stored, overflow, dlg_tok = simulate_store(
                        n, memory_max=mem, user_chars=uc, assistant_chars=ac, long_every=le, long_chars=lc
                    )
                    ti = n  # turn_index approx
                    fire, reasons = would_compact(
                        prompt_overhead=overhead,
                        dlg_tokens=dlg_tok,
                        stored_msgs=len(stored),
                        turn_index=ti,
                        budget=budget,
                        threshold=thr,
                        turn_limit=tl,
                        min_msgs=4,
                    )
                    marker = "MARKER-Z9"
                    kept = marker in str(stored)
                    rows.append(
                        GridRow(
                            memory_max=mem,
                            budget=budget,
                            threshold=thr,
                            turn_limit=tl,
                            scenario=sc_name,
                            n_turns=n,
                            stored_msgs=len(stored),
                            overflow_msgs=overflow,
                            dialogue_tokens=dlg_tok,
                            prompt_est=overhead + dlg_tok,
                            compact_fires=fire,
                            reasons=reasons,
                            constraint_kept_in_store=kept,
                        )
                    )

    # Score configs: maximize fire on long scenarios, minimize fire on chitchat_15, overflow>0 on marker
    configs: Dict[str, Dict[str, Any]] = {}
    for mem in memory_opts:
        for thr in threshold_opts:
            for tl in turn_limit_opts:
                key = f"m{mem}_t{thr}_tl{tl}"
                subset = [r for r in rows if r.memory_max == mem and r.threshold == thr and r.turn_limit == tl]
                fires_long = sum(1 for r in subset if r.scenario in ("paste_10", "heavy_12", "marker_test_20") and r.compact_fires)
                fires_short = sum(1 for r in subset if r.scenario == "chitchat_15" and r.compact_fires)
                overflow_marker = next((r.overflow_msgs for r in subset if r.scenario == "marker_test_20"), 0)
                avg_dlg = sum(r.dialogue_tokens for r in subset if r.scenario == "chitchat_15") / max(1, len([r for r in subset if r.scenario == "chitchat_15"]))
                score = fires_long * 3 - fires_short * 2 + (1 if overflow_marker > 0 else 0) - avg_dlg / 500
                configs[key] = {
                    "memory_max": mem,
                    "threshold": thr,
                    "turn_limit": tl,
                    "score": round(score, 2),
                    "fires_long": fires_long,
                    "fires_short_chitchat_15": fires_short,
                    "overflow_on_20_turns": overflow_marker,
                    "avg_chitchat15_dialogue_tokens": round(avg_dlg, 1),
                }

    best = max(configs.items(), key=lambda x: x[1]["score"])
    recommendation = {
        "chosen": {
            "DIALOGUE_MEMORY_MAX": best[1]["memory_max"],
            "COMPACTOR_BUDGET_TOKENS": budget,
            "COMPACTOR_THRESHOLD": best[1]["threshold"],
            "COMPACTOR_TURN_LIMIT": best[1]["turn_limit"],
            "COMPACTOR_MIN_DIALOGUE_MESSAGES": 4,
            "DIALOGUE_SUMMARY_ON_OVERFLOW": True,
            "DIALOGUE_ARCHIVE_BACKFILL_ENABLED": True,
        },
        "rationale": (
            "Maximize compactor on long/paste threads; avoid firing on normal 15-turn chitchat; "
            "FIFO overflow on 20 turns enables persist summary path."
        ),
        "best_key": best[0],
        "best_metrics": best[1],
        "top3": sorted(configs.items(), key=lambda x: x[1]["score"], reverse=True)[:3],
    }

    out = Path("data/benchmarks/compaction_params_research.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"recommendation": recommendation, "grid_sample": [asdict(r) for r in rows[:40]], "config_scores": configs}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(recommendation, ensure_ascii=False, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
