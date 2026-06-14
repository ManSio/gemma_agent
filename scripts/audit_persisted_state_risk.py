#!/usr/bin/env python3
"""Аудит персистентного состояния: что может тащить старые баги после деплоя."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _behavior_dir() -> Path:
    base = Path(os.getenv("BEHAVIOR_DATA_DIR", "./data/users")).resolve()
    return base / "behavior"


def _scan_behavior_sessions() -> List[Dict[str, Any]]:
    bdir = _behavior_dir()
    if not bdir.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for fp in sorted(bdir.glob("*.json")):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rows.append({"file": fp.name, "error": "load_failed"})
            continue
        if not isinstance(raw, dict):
            continue
        rp = raw.get("routing_prefs") if isinstance(raw.get("routing_prefs"), dict) else {}
        slot = rp.get("dialogue_slot") if isinstance(rp.get("dialogue_slot"), dict) else {}
        epoch = raw.get("conversation_epoch") if isinstance(raw.get("conversation_epoch"), dict) else {}
        rows.append(
            {
                "file": fp.name,
                "recent_n": len(raw.get("recent_messages") or []),
                "summary_len": len(str(raw.get("dialogue_summary") or "")),
                "facts_n": len(raw.get("user_facts") or {}),
                "pending_correction": bool(rp.get("pending_correction")),
                "dialogue_slot": slot.get("kind") if slot else None,
                "slot_turns_left": slot.get("turns_left") if slot else None,
                "prefer_general_over_math": bool(rp.get("prefer_general_over_math")),
                "epoch_id": int(epoch.get("id") or 0),
                "session_task_module": (raw.get("session_task") or {}).get("last_module")
                if isinstance(raw.get("session_task"), dict)
                else None,
                "weather_anchor": bool(raw.get("weather_anchor")),
                "impression_turns": int((raw.get("user_agent_impression") or {}).get("counters", {}).get("turns_recorded") or 0)
                if isinstance(raw.get("user_agent_impression"), dict)
                else 0,
            }
        )
    return rows


def _legacy_lesson_risk() -> Dict[str, Any]:
    from core.ephemeral_lessons import load_document, snapshot_for_operator
    from core.feedback_contract import lesson_applies_in_context

    doc = load_document()
    active = [x for x in (doc.get("lessons") or []) if isinstance(x, dict) and x.get("active", True)]
    legacy_generic = 0
    anchor_lessons = 0
    blocked_on_followup = 0
    ctx = {
        "discourse_resolution": {"last_user_q": "Почему земля круглая и как это доказали?"},
        "recent_dialogue": [
            {"role": "user", "text": "Почему земля круглая и как это доказали?"},
            {"role": "assistant", "text": "Земля почти сферическая из-за гравитации."},
        ],
    }
    for le in active:
        meta = le.get("meta") if isinstance(le.get("meta"), dict) else {}
        if str(meta.get("anchor_user_q") or "").strip():
            anchor_lessons += 1
        else:
            legacy_generic += 1
        if lesson_applies_in_context(le, "почему так произошло?", ctx):
            blocked_on_followup += 0
        else:
            inst = str(le.get("instruction") or "").lower()
            if "исправь подход" in inst or not meta.get("anchor_user_q"):
                blocked_on_followup += 1
    snap = snapshot_for_operator()
    return {
        **snap,
        "legacy_generic_active": legacy_generic,
        "anchor_lessons_active": anchor_lessons,
        "lessons_blocked_on_generic_followup_sim": blocked_on_followup,
    }


def _runtime_files() -> List[Tuple[str, bool, int]]:
    runtime = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime")).resolve()
    names = (
        "ephemeral_lessons.json",
        "ephemeral_pending.json",
        "usage_learning_state.json",
        "system_directive_addon.txt",
        "operator_rules.json",
        "light_reminders.json",
    )
    out: List[Tuple[str, bool, int]] = []
    for name in names:
        p = runtime / name
        out.append((name, p.is_file(), p.stat().st_size if p.is_file() else 0))
    return out


def main() -> int:
    print("=== Persisted state risk audit ===\n")
    sessions = _scan_behavior_sessions()
    print(f"behavior sessions: {len(sessions)} dir={_behavior_dir()}")
    risky = [
        s
        for s in sessions
        if s.get("pending_correction")
        or s.get("dialogue_slot")
        or int(s.get("recent_n") or 0) > 12
        or int(s.get("summary_len") or 0) > 400
    ]
    print(f"  sessions with sticky state (slot/correction/long stm): {len(risky)}")
    for s in risky[:8]:
        print(f"    - {s}")

    print("\nephemeral lessons:")
    try:
        el = _legacy_lesson_risk()
        for k, v in el.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  error: {e}")

    print("\nruntime files:")
    for name, exists, size in _runtime_files():
        print(f"  {name}: exists={exists} size={size}")

    print("\nrecommendations:")
    print("  1. Full wipe NOT required on deploy — code guards filter legacy lessons.")
    print("  2. Run deactivate_legacy_ephemeral_lessons.py after rating-contract deploy.")
    print("  3. Sticky dialogue: /new or idle TTL (CONVERSATION_EPOCH_IDLE_TTL_SEC).")
    print("  4. user_facts: keep; validate with can_persist_user_fact on write.")
    print("  5. usage_learning / Qdrant cache: aggregates only, not bug patches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
