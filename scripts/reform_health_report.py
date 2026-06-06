#!/usr/bin/env python3
"""
Проверка brain-centric реформы по turns.jsonl и runtime_errors.

  python scripts/reform_health_report.py --since 2026-05-25T17:33:00+00:00
  python scripts/reform_health_report.py --remote --since 2026-05-25T17:33:00+00:00

Бан-лист planner bypass (должен быть 0 после реформы): news_direct, news_item_direct,
weather_direct, geo_nearby, affirmative_search.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BANNED_BYPASS = frozenset(
    {
        "news_direct",
        "news_item_direct",
        "weather_direct",
        "geo_nearby",
        "affirmative_search",
    }
)


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        s = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def analyze_turns(path: Path, since: datetime) -> Dict[str, Any]:
    outcomes = Counter()
    profiles = Counter()
    bypass = Counter()
    facts_hints = 0
    n = 0
    if not path.is_file():
        return {"error": f"missing {path}", "turns": 0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t.get("type") == "scenario":
            continue
        dt = _parse_ts(str(t.get("ts") or ""))
        if dt is None or dt < since:
            continue
        n += 1
        outcomes[t.get("outcome") or "?"] += 1
        profiles[t.get("profile") or "?"] += 1
        pb = str(t.get("planner_bypass") or "").strip()
        if pb:
            bypass[pb] += 1
        ue = str(t.get("user_excerpt") or "")
        ae = str(t.get("assistant_excerpt") or "")
        # Только длинная вставка (§9): короткий чат с «страна» в ответе не считаем регрессией.
        if len(ue) >= 400 and "запомнить" in ae.lower() and "стран" in ae.lower():
            facts_hints += 1
    banned_hits = {k: bypass[k] for k in bypass if k in _BANNED_BYPASS}
    clean = not banned_hits and facts_hints == 0
    return {
        "turns": n,
        "outcomes": dict(outcomes),
        "profiles_top": profiles.most_common(10),
        "planner_bypass": dict(bypass),
        "banned_bypass_hits": banned_hits,
        "country_confirm_hints": facts_hints,
        "ok": clean,
        "has_traffic": n > 0,
        "traffic_ok": n > 0 and clean,
    }


def analyze_errors(path: Path, since: datetime) -> Dict[str, Any]:
    top = Counter()
    news = 0
    n = 0
    if not path.is_file():
        return {"count": 0, "news_rss": 0, "top": []}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        dt = _parse_ts(str(e.get("ts") or ""))
        if dt is None or dt < since:
            continue
        n += 1
        msg = str(e.get("message") or "")[:64]
        comp = str(e.get("component") or "")
        top[f"{comp}:{msg}"] += 1
        if "news_" in msg or "rss" in msg.lower():
            news += 1
    return {"count": n, "news_rss": news, "top": top.most_common(8)}


def _ssh_target(alias: str) -> str:
    priv = _ROOT / "docs" / "OPS_PRIVATE.local.md"
    key = f"{alias}_SSH"
    if priv.is_file():
        for line in priv.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return alias


def _remote_paths(host: str, since_iso: str) -> Dict[str, Any]:
    ssh = _ssh_target(host)
    script = (
        f"cd /opt/gemma_agent && venv/bin/python3 scripts/reform_health_report.py "
        f"--since '{since_iso}' --json"
    )
    raw = subprocess.check_output(
        ["ssh", "-o", "ConnectTimeout=12", ssh, script],
        text=True,
    )
    return json.loads(raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--since", required=True, help="ISO UTC, напр. 2026-05-25T17:33:00+00:00")
    ap.add_argument("--remote", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    since = _parse_ts(args.since)
    if since is None:
        print("bad --since", file=sys.stderr)
        return 2
    root = Path(args.root)
    if args.remote:
        doc = {
            "since": args.since,
            "hosts": {
                "HOST_LAN": _remote_paths("HOST_LAN", args.since),
                "VPS_PROD": _remote_paths("VPS_PROD", args.since),
            },
        }
        if args.json:
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        else:
            for h, rep in doc["hosts"].items():
                print(f"\n=== {h} ===")
                print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    rep = {
        "since": args.since,
        "turns": analyze_turns(root / "data/runtime/turns.jsonl", since),
        "errors": analyze_errors(root / "data/runtime_errors.jsonl", since),
    }
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    turns = rep["turns"]
    if turns.get("error"):
        return 2
    if not turns.get("ok", True):
        return 1
    if not turns.get("has_traffic"):
        print("note: no turns in window (ok if deploy just happened)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
