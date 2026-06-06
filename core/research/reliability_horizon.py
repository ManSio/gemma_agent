"""METR-inspired reliability horizon from turns.jsonl (offline).

Adaptation for gemma_bot:
  - METR: wall-clock task duration at 50% success rate.
  - Here: consecutive successful *turns* per session at 50% session coverage,
    plus optional median streak duration in minutes from timestamps.

Success turn = outcome ok, no issues, delivery_ok not False, no negative feedback.
"""
from __future__ import annotations

import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional


def _parse_ts(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        t = datetime.fromisoformat(s)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except ValueError:
        return None


def turn_is_success(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("type") == "scenario":
        return False
    if row.get("user_feedback_negative"):
        return False
    issues = row.get("issues") or []
    if isinstance(issues, list) and issues:
        return False
    if row.get("delivery_ok") is False:
        return False
    oc = str(row.get("outcome") or "").strip().lower()
    if oc in ("failure", "error", "fallback"):
        return False
    if oc == "clarify":
        return False
    if oc == "ok":
        return True
    return bool(row.get("ok"))


def max_consecutive_success(rows: List[dict]) -> int:
    best = cur = 0
    for r in rows:
        if turn_is_success(r):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def streak_duration_minutes(rows: List[dict]) -> float:
    """Wall-clock span of longest success streak in one session."""
    best_start: Optional[datetime] = None
    best_end: Optional[datetime] = None
    cur_start: Optional[datetime] = None
    cur_end: Optional[datetime] = None
    cur_len = best_len = 0

    for r in rows:
        ts = _parse_ts(r.get("ts"))
        if turn_is_success(r):
            if cur_len == 0:
                cur_start = ts
            cur_end = ts
            cur_len += 1
            if cur_len > best_len:
                best_len = cur_len
                best_start, best_end = cur_start, cur_end
        else:
            cur_len = 0
            cur_start = cur_end = None

    if best_start and best_end and best_end >= best_start:
        return (best_end - best_start).total_seconds() / 60.0
    return 0.0


def horizon_turn_count(session_max_streaks: List[int]) -> int:
    """Largest k where >=50% sessions have max streak >= k (METR 50% analogue)."""
    if not session_max_streaks:
        return 0
    n = len(session_max_streaks)
    for k in range(max(session_max_streaks), 0, -1):
        share = sum(1 for s in session_max_streaks if s >= k) / n
        if share >= 0.5:
            return k
    return 0


def horizon_minutes(durations: List[float]) -> float:
    """50th percentile of longest streak duration per session (minutes)."""
    positive = [d for d in durations if d > 0]
    if not positive:
        return 0.0
    return float(statistics.median(positive))


def _session_key(row: dict) -> str:
    uid = str(row.get("user_id") or "unknown")
    gid = row.get("group_id")
    if gid is not None and str(gid).strip():
        return f"{uid}:g{gid}"
    return uid


def split_sessions(
    rows: List[dict],
    *,
    gap_minutes: int,
) -> List[List[dict]]:
    if not rows:
        return []
    ordered = sorted(
        [r for r in rows if _parse_ts(r.get("ts"))],
        key=lambda r: _parse_ts(r.get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not ordered:
        return [rows]

    gap = timedelta(minutes=max(1, gap_minutes))
    sessions: List[List[dict]] = []
    cur: List[dict] = [ordered[0]]
    last_ts = _parse_ts(ordered[0].get("ts"))

    for r in ordered[1:]:
        ts = _parse_ts(r.get("ts"))
        if ts and last_ts and ts - last_ts > gap:
            sessions.append(cur)
            cur = [r]
        else:
            cur.append(r)
        if ts:
            last_ts = ts
    if cur:
        sessions.append(cur)
    return sessions


def iter_turn_rows(path: Path, *, cutoff: Optional[datetime] = None) -> Iterable[dict]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as f:
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
            ts = _parse_ts(row.get("ts"))
            if cutoff and ts and ts < cutoff:
                continue
            yield row


def compute_horizon_report(
    turns_path: Path,
    *,
    days: int = 7,
    session_gap_minutes: Optional[int] = None,
    user_id_filter: str = "",
) -> Dict[str, Any]:
    gap = session_gap_minutes
    if gap is None:
        try:
            gap = int(os.getenv("AGENT_RELIABILITY_SESSION_GAP_MIN", "30"))
        except ValueError:
            gap = 30

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    by_user: DefaultDict[str, List[dict]] = defaultdict(list)
    n_rows = 0
    outcomes: Counter = Counter()

    for row in iter_turn_rows(turns_path, cutoff=cutoff):
        n_rows += 1
        outcomes[str(row.get("outcome") or "?")] += 1
        uid = str(row.get("user_id") or "")
        if user_id_filter and uid != user_id_filter.strip():
            continue
        by_user[_session_key(row)].append(row)

    session_streaks: List[int] = []
    session_durations: List[float] = []
    per_user_best: Dict[str, int] = {}

    for key, rows in by_user.items():
        for sess in split_sessions(rows, gap_minutes=gap):
            mx = max_consecutive_success(sess)
            session_streaks.append(mx)
            session_durations.append(streak_duration_minutes(sess))
        if rows:
            per_user_best[key] = max(max_consecutive_success(s) for s in split_sessions(rows, gap_minutes=gap))

    h_turns = horizon_turn_count(session_streaks)
    h_min = horizon_minutes(session_durations)

    by_profile: Dict[str, Dict[str, Any]] = {}
    prof_rows: DefaultDict[str, List[dict]] = defaultdict(list)
    for rows in by_user.values():
        for r in rows:
            p = str(r.get("profile") or "?").strip() or "?"
            prof_rows[p].append(r)
    for prof, rows in prof_rows.items():
        ok_n = sum(1 for r in rows if turn_is_success(r))
        by_profile[prof] = {
            "turns": len(rows),
            "success_pct": round(100.0 * ok_n / len(rows), 1) if rows else 0.0,
        }

    article_turns = [
        r
        for rows in by_user.values()
        for r in rows
        if r.get("article_thread_subject") or r.get("planner_bypass", "").startswith("article")
    ]
    article_ok = sum(1 for r in article_turns if turn_is_success(r))

    return {
        "schema": "gemma_reliability_horizon_v1",
        "metr_analogue": "50% sessions achieve >= N consecutive successful turns",
        "window_days": days,
        "session_gap_minutes": gap,
        "turns_path": str(turns_path),
        "turns_read": n_rows,
        "sessions_n": len(session_streaks),
        "outcome_counts": dict(outcomes.most_common(12)),
        "horizon_turns_50pct": h_turns,
        "horizon_streak_minutes_median": round(h_min, 2),
        "session_max_streak": {
            "p50": int(statistics.median(session_streaks)) if session_streaks else 0,
            "p90": int(
                sorted(session_streaks)[max(0, int(len(session_streaks) * 0.9) - 1)]
            )
            if session_streaks
            else 0,
            "max": max(session_streaks) if session_streaks else 0,
        },
        "per_user_max_streak": dict(sorted(per_user_best.items(), key=lambda x: -x[1])[:20]),
        "by_profile": by_profile,
        "article_thread": {
            "turns": len(article_turns),
            "success_pct": round(100.0 * article_ok / len(article_turns), 1) if article_turns else None,
        },
        "interpretation": _interpret(h_turns, h_min, session_streaks),
    }


def _interpret(h_turns: int, h_min: float, streaks: List[int]) -> str:
    if not streaks:
        return "Нет ходов в окне — запустите бота или расширьте --days."
    if h_turns >= 5:
        level = "strong"
    elif h_turns >= 3:
        level = "moderate"
    else:
        level = "fragile"
    return (
        f"Уровень {level}: в >=50% сессий бот держит {h_turns} успешных ходов подряд; "
        f"медиана длительности лучшей серии ~{h_min:.0f} мин."
    )
