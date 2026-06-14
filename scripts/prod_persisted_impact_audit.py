#!/usr/bin/env python3
"""Forensic audit: как персистентное состояние коррелирует с плохими ходами на prod."""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CORRECTION_RE = re.compile(
    r"(?i)^(нет|не то|не так|не про|wrong|неправильно|"
    r"ты не понял|не понял|не то что|это не то|исправь)"
)
_BAD_ISSUES = frozenset(
    {
        "user_feedback_negative",
        "outcome_clarify",
        "outcome_fallback",
        "product_behavior",
        "semantic_failure",
        "short_reply",
        "prompt_leak_suspect",
    }
)


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Parse ISO timestamp to UTC datetime."""
    if raw is None:
        return None
    try:
        s = str(raw).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "—"
    return f"{100.0 * n / d:.1f}%"


def _load_jsonl(path: Path, cut: datetime) -> List[Dict[str, Any]]:
    """Load JSONL rows newer than cut."""
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            dt = _parse_ts(row.get("ts") or row.get("timestamp"))
            if dt is not None and dt < cut:
                continue
            rows.append(row)
    return rows


def _behavior_dir(root: Path) -> Path:
    base = Path(os.getenv("BEHAVIOR_DATA_DIR") or root / "data" / "users")
    for rel in ("behavior", "users/behavior"):
        p = base / rel.split("/")[-1] if "/" not in rel else base.parent / rel
        if rel == "behavior":
            p = base / "behavior" if (base / "behavior").is_dir() else root / "data" / "users" / "behavior"
        if p.is_dir():
            return p
    return root / "data" / "users" / "behavior"


def _user_id_from_behavior_file(name: str) -> Tuple[str, Optional[str]]:
    """Parse user_id and group from behavior filename."""
    stem = name[:-5] if name.endswith(".json") else name
    if "__" not in stem:
        return stem, None
    u, g = stem.split("__", 1)
    return u, None if g == "dm" else g


def _scan_behavior_sessions(beh_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load risk snapshot per behavior file."""
    out: Dict[str, Dict[str, Any]] = {}
    if not beh_dir.is_dir():
        return out
    for fp in sorted(beh_dir.glob("*.json")):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        uid, gid = _user_id_from_behavior_file(fp.name)
        key = f"{uid}__{gid or 'dm'}"
        rp = raw.get("routing_prefs") if isinstance(raw.get("routing_prefs"), dict) else {}
        slot = rp.get("dialogue_slot") if isinstance(rp.get("dialogue_slot"), dict) else {}
        epoch = raw.get("conversation_epoch") if isinstance(raw.get("conversation_epoch"), dict) else {}
        risks: List[str] = []
        if slot.get("kind"):
            risks.append(f"dialogue_slot:{slot.get('kind')}")
        if rp.get("pending_correction"):
            risks.append("pending_correction")
        if int(slot.get("turns_left") or 0) > 0:
            risks.append("slot_turns_left")
        summary = str(raw.get("dialogue_summary") or "")
        if len(summary) > 400:
            risks.append("long_dialogue_summary")
        if len(raw.get("recent_messages") or []) > 14:
            risks.append("fat_recent_messages")
        if rp.get("prefer_general_over_math"):
            risks.append("prefer_general_over_math")
        if raw.get("weather_anchor"):
            risks.append("weather_anchor")
        autolearn = raw.get("ephemeral_autolearn") if isinstance(raw.get("ephemeral_autolearn"), dict) else {}
        buckets = autolearn.get("buckets") if isinstance(autolearn.get("buckets"), dict) else {}
        if buckets:
            risks.append("ephemeral_autolearn_buckets")
        out[key] = {
            "file": fp.name,
            "user_id": uid,
            "group_id": gid,
            "recent_n": len(raw.get("recent_messages") or []),
            "summary_len": len(summary),
            "facts_n": len(raw.get("user_facts") or {}),
            "epoch_id": int(epoch.get("id") or 0),
            "slot_kind": str(slot.get("kind") or ""),
            "slot_turns_left": int(slot.get("turns_left") or 0),
            "risks": risks,
        }
    return out


def _ephemeral_inventory() -> Dict[str, Any]:
    """Active ephemeral lessons with legacy/anchor classification."""
    from core.ephemeral_lessons import load_document, snapshot_for_operator

    doc = load_document()
    active: List[Dict[str, Any]] = []
    legacy = 0
    anchor = 0
    broad_triggers = 0
    for le in doc.get("lessons") or []:
        if not isinstance(le, dict) or not le.get("active", True):
            continue
        meta = le.get("meta") if isinstance(le.get("meta"), dict) else {}
        trig = str(le.get("trigger") or "")
        has_anchor = bool(str(meta.get("anchor_user_q") or "").strip())
        if has_anchor:
            anchor += 1
        else:
            legacy += 1
        if le.get("match") != "regex" and 0 < len(trig) < 8:
            broad_triggers += 1
        active.append(
            {
                "id": le.get("id"),
                "trigger": trig[:64],
                "match": le.get("match"),
                "has_anchor": has_anchor,
                "failure_class": meta.get("failure_class"),
                "source": meta.get("source"),
                "hit_count": int(le.get("hit_count") or 0),
            }
        )
    snap = snapshot_for_operator()
    return {
        **snap,
        "legacy_active": legacy,
        "anchor_active": anchor,
        "broad_trigger_active": broad_triggers,
        "lessons": sorted(active, key=lambda x: -int(x.get("hit_count") or 0))[:40],
    }


def _context_from_turn(turn: Dict[str, Any], ops_by_trace: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Reconstruct minimal context for feedback_contract filters."""
    ctx: Dict[str, Any] = {}
    tid = str(turn.get("trace_id") or turn.get("fp") or "")
    ops = ops_by_trace.get(tid) if tid else None
    if isinstance(ops, dict):
        rb = ops.get("recent_before")
        if isinstance(rb, list):
            ctx["recent_dialogue"] = rb
    for k in ("discourse_resolution", "turn_meaning", "session_task", "conversation_epoch"):
        if isinstance(turn.get(k), dict):
            ctx[k] = turn[k]
    if turn.get("topic_anchor"):
        ctx.setdefault("discourse_resolution", {})["last_user_q"] = turn["topic_anchor"]
    return ctx


def _lesson_hits_for_text(
    text: str,
    context: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Which active lessons would enter brain prompt for this text."""
    from core.feedback_contract import collect_lessons_for_context, lesson_applies_in_context

    hits: List[Dict[str, Any]] = []
    for le in collect_lessons_for_context(text or "", context):
        if lesson_applies_in_context(le, text or "", context):
            hits.append(
                {
                    "id": le.get("id"),
                    "trigger": str(le.get("trigger") or "")[:48],
                    "has_anchor": bool((le.get("meta") or {}).get("anchor_user_q")),
                    "instruction_head": str(le.get("instruction") or "")[:80],
                }
            )
    return hits


def _turn_is_bad(turn: Dict[str, Any]) -> bool:
    issues = [str(x) for x in (turn.get("issues") or [])]
    if any(i in _BAD_ISSUES for i in issues):
        return True
    if turn.get("outcome") in ("clarify", "fallback"):
        return True
    ue = str(turn.get("user_excerpt") or "")
    if _CORRECTION_RE.search(ue[:120]):
        return True
    otg = turn.get("outbound_thread_guard_issues") or []
    return bool(otg)


def _persisted_factors_for_turn(
    turn: Dict[str, Any],
    behavior: Dict[str, Dict[str, Any]],
    lesson_hits: List[Dict[str, Any]],
) -> List[str]:
    """Tag which persisted layers could explain a bad turn."""
    factors: List[str] = []
    uid = str(turn.get("user_id") or "")
    gid = turn.get("group_id")
    bkey = f"{uid}__{gid or 'dm'}"
    beh = behavior.get(bkey) or behavior.get(f"{uid}__dm")
    if isinstance(beh, dict):
        for r in beh.get("risks") or []:
            factors.append(f"behavior:{r}")
    dsk = str(turn.get("dialogue_slot_kind") or "").strip()
    if dsk:
        factors.append(f"turn_slot:{dsk}")
    if turn.get("correction_pending"):
        factors.append("turn:correction_pending")
    if turn.get("kv_reset_reason"):
        factors.append(f"kv_reset:{turn.get('kv_reset_reason')}")
    legacy_hits = [h for h in lesson_hits if not h.get("has_anchor")]
    anchor_hits = [h for h in lesson_hits if h.get("has_anchor")]
    if legacy_hits:
        factors.append(f"ephemeral_legacy_hits:{len(legacy_hits)}")
    if anchor_hits:
        factors.append(f"ephemeral_anchor_hits:{len(anchor_hits)}")
    if turn.get("last_feedback_applied"):
        factors.append("turn:last_feedback_applied")
    return factors


def audit_persisted_impact(
    root: Path,
    *,
    days: int = 14,
    user_id: str = "",
) -> Dict[str, Any]:
    """Correlate turns.jsonl bad outcomes with behavior + ephemeral state."""
    cut = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    turns_path = root / "data/runtime/turns.jsonl"
    ops_path = root / "data/runtime/ops_trace.jsonl"
    turns = _load_jsonl(turns_path, cut)
    ops_rows = _load_jsonl(ops_path, cut)
    ops_by_trace = {
        str(r.get("trace_id") or ""): r
        for r in ops_rows
        if str(r.get("trace_id") or "").strip()
    }

    real = [
        t
        for t in turns
        if t.get("type") not in ("scenario", "pre_send")
        and str(t.get("user_excerpt") or "").strip()
    ]
    if user_id:
        real = [t for t in real if str(t.get("user_id") or "") == user_id]

    behavior = _scan_behavior_sessions(_behavior_dir(root))
    ephemeral = _ephemeral_inventory()

    bad_turns: List[Dict[str, Any]] = []
    good_turns = 0
    factor_counter: Counter[str] = Counter()
    explained = 0
    lesson_hit_turns = 0
    slot_turns = 0

    for t in real:
        ue = str(t.get("user_excerpt") or "")
        ctx = _context_from_turn(t, ops_by_trace)
        hits = _lesson_hits_for_text(ue, ctx)
        if hits:
            lesson_hit_turns += 1
        if t.get("dialogue_slot_kind"):
            slot_turns += 1
        is_bad = _turn_is_bad(t)
        if not is_bad:
            good_turns += 1
            continue
        factors = _persisted_factors_for_turn(t, behavior, hits)
        for f in factors:
            factor_counter[f.split(":")[0] + ":" + f.split(":")[1] if ":" in f else f] += 1
        if factors:
            explained += 1
        bad_turns.append(
            {
                "ts": t.get("ts"),
                "trace_id": t.get("trace_id") or t.get("fp"),
                "user_id": t.get("user_id"),
                "outcome": t.get("outcome"),
                "issues": t.get("issues"),
                "dialogue_slot_kind": t.get("dialogue_slot_kind"),
                "user_excerpt": ue[:100],
                "assistant_excerpt": str(t.get("assistant_excerpt") or "")[:120],
                "persisted_factors": factors,
                "ephemeral_hits": hits[:5],
            }
        )

    bad_turns.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)

    # Session age vs issues: correlate recent_n with bad turn rate per user
    by_user_bad: Counter[str] = Counter()
    by_user_all: Counter[str] = Counter()
    for t in real:
        uid = str(t.get("user_id") or "")
        by_user_all[uid] += 1
        if _turn_is_bad(t):
            by_user_bad[uid] += 1

    user_risk_rows: List[Dict[str, Any]] = []
    for bkey, snap in behavior.items():
        uid = str(snap.get("user_id") or "")
        total = by_user_all.get(uid, 0)
        bad = by_user_bad.get(uid, 0)
        user_risk_rows.append(
            {
                "user_id": uid,
                "behavior_risks": snap.get("risks") or [],
                "recent_n": snap.get("recent_n"),
                "summary_len": snap.get("summary_len"),
                "turns_in_window": total,
                "bad_turns_in_window": bad,
                "bad_rate": round(bad / total, 3) if total else None,
            }
        )
    user_risk_rows.sort(
        key=lambda r: (r.get("bad_turns_in_window") or 0, len(r.get("behavior_risks") or [])),
        reverse=True,
    )

    sticky_sessions = sum(1 for s in behavior.values() if s.get("risks"))
    verdict = {
        "bad_turns": len(bad_turns),
        "good_turns": good_turns,
        "bad_turns_with_persisted_factor": explained,
        "persisted_explained_pct": _pct(explained, len(bad_turns)),
        "turns_with_ephemeral_hit": lesson_hit_turns,
        "ephemeral_hit_pct": _pct(lesson_hit_turns, len(real)),
        "turns_with_dialogue_slot": slot_turns,
        "behavior_sessions_with_risks": sticky_sessions,
        "behavior_sessions_total": len(behavior),
        "interpretation": (
            "If persisted_explained_pct is high, cleanup (/new, deactivate lessons) likely helps. "
            "If low, issues are mostly fresh code-path/LLM — wipe won't fix."
        ),
    }

    return {
        "window": {"days": days, "cutoff_utc": cut.isoformat()},
        "root": str(root),
        "totals": {"turns_real": len(real), "ops_trace_rows": len(ops_rows)},
        "verdict": verdict,
        "ephemeral": ephemeral,
        "factor_counts": factor_counter.most_common(25),
        "user_risk": user_risk_rows[:20],
        "bad_turn_samples": bad_turns[:25],
        "issues_top": Counter(
            i for t in real for i in (t.get("issues") or [])
        ).most_common(20),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Prod forensic: persisted state vs bad turns")
    ap.add_argument("--root", default=str(_ROOT), help="Repo root (/srv/gemma_bot on VPS)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--user-id", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    report = audit_persisted_impact(root, days=args.days, user_id=args.user_id.strip())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
