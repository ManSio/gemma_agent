#!/usr/bin/env python3
"""
Сброс «мусора» агента для одного пользователя (и опционально глобальных кэшей).

  python scripts/reset_user_agent_state.py --user-id "$PROBE_USER_ID"
  python scripts/reset_user_agent_state.py --user-id "$PROBE_USER_ID" --group-id dm
  python scripts/reset_user_agent_state.py --user-id "$PROBE_USER_ID" --also-router --also-llm-cache

Не трогает: долгие user_facts (для полного сброса фактов — /facts_reset в боте).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ.setdefault("GEMMA_PROJECT_ROOT", str(_ROOT))


def _archive_path(user_id: str, group_id: Optional[str]) -> Path:
    from core.message_archive import _path

    return Path(_path(user_id, group_id))


def _behavior_path(user_id: str, group_id: Optional[str]) -> Path:
    from core.behavior_store import BehaviorStore

    return Path(BehaviorStore()._path(user_id, group_id))


def reset_user(
    user_id: str,
    group_id: Optional[str],
    *,
    clear_archive: bool,
    clear_recent: bool,
    reset_kv: bool,
    reset_dialog: bool,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {"user_id": user_id, "group_id": group_id, "steps": []}

    if clear_recent:
        from core.behavior_store import BehaviorStore

        bs = BehaviorStore()
        rec = bs.load(user_id, group_id)
        before = len(rec.get("recent_messages") or [])
        rec["recent_messages"] = []
        rec["topic_tracking"] = {"current": "", "snippet": ""}
        bs.save(user_id, group_id, rec)
        report["steps"].append(f"recent_messages cleared ({before} -> 0)")

    if clear_archive:
        ap = _archive_path(user_id, group_id)
        if ap.is_file():
            n = 0
            try:
                raw = json.loads(ap.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    n = len(raw.get("items") or [])
                elif isinstance(raw, list):
                    n = len(raw)
            except Exception:
                n = -1
            ap.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")
            report["steps"].append(f"message_archive truncated ({n} items -> 0) @ {ap}")
        else:
            report["steps"].append(f"message_archive missing @ {ap}")

    if reset_kv:
        from core.brain.session_stickiness import force_session_reset

        sid = force_session_reset(
            user_id=user_id, group_id=group_id, reason="admin_reset_user_agent_state"
        )
        report["steps"].append(f"kv_session reset -> {sid}")

    if reset_dialog:
        from core.dialog_state import reset_dialog_state

        reset_dialog_state("admin_reset_user_agent_state", user_id=user_id, group_id=group_id)
        report["steps"].append("dialog_state reset")

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset per-user agent dialogue caches")
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--group-id", default=None, help="None or dm for private chat")
    ap.add_argument("--no-archive", action="store_true", help="Keep message_archive file")
    ap.add_argument("--no-recent", action="store_true", help="Keep recent_messages")
    ap.add_argument("--no-kv", action="store_true", help="Skip KV session epoch bump")
    ap.add_argument("--no-dialog", action="store_true", help="Skip in-memory dialog_state")
    ap.add_argument(
        "--also-router",
        action="store_true",
        help="Global: clear router LRU (all users until re-learned)",
    )
    ap.add_argument(
        "--also-llm-cache",
        action="store_true",
        help="Global: wipe SQLite llm_cache",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    gid = args.group_id
    if gid and gid.lower() in ("dm", "none", ""):
        gid = None

    report = reset_user(
        args.user_id,
        gid,
        clear_archive=not args.no_archive,
        clear_recent=not args.no_recent,
        reset_kv=not args.no_kv,
        reset_dialog=not args.no_dialog,
    )
    report["behavior_path"] = str(_behavior_path(args.user_id, gid))
    report["archive_path"] = str(_archive_path(args.user_id, gid))

    if args.also_router:
        from core.brain.router_classifier import _lru_clear, reset_metrics, trigger_frequency_sweep

        n = _lru_clear()
        reset_metrics()
        trigger_frequency_sweep()
        report["steps"].append(f"router LRU cleared ({n} entries)")

    if args.also_llm_cache:
        from core.llm_cache import invalidate_on_reset

        n = invalidate_on_reset("reset_user_agent_state")
        report["steps"].append(f"llm_cache invalidated ({n} rows)")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"user_id={args.user_id} group_id={gid or 'dm'}")
        for s in report["steps"]:
            print(f"  - {s}")
        print(f"behavior: {report['behavior_path']}")
        print(f"archive:  {report['archive_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
