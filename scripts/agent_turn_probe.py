#!/usr/bin/env python3
"""
Прогон одного хода через тот же orchestrator, что и бот, + пост-обработка как в Telegram.

  python scripts/agent_turn_probe.py --user-id "$PROBE_USER_ID" --text "напиши функцию факториала"
  python scripts/agent_turn_probe.py --user-id "$PROBE_USER_ID" --text "мусор" --bug-pending
  python scripts/agent_turn_probe.py --user-id "$PROBE_USER_ID" --text "..." --json-out /tmp/turn.json

Требует: .env в корне проекта, QDRANT_* (как у main.py). Первый запуск тяжёлый (загрузка модулей).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ.setdefault("GEMMA_PROJECT_ROOT", str(_ROOT))


def _tail_turns_jsonl(n: int = 3) -> List[Dict[str, Any]]:
    p = _ROOT / "data" / "runtime" / "turns.jsonl"
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _outputs_to_dict(outputs: List[Any], user_text: str) -> List[Dict[str, Any]]:
    from core.brain.response_finalize import finalize_user_reply
    from core.input_layer import _apply_anti_intrusion_guard

    guarded, silent = _apply_anti_intrusion_guard(user_text, outputs)
    rows: List[Dict[str, Any]] = []
    for o in guarded:
        meta = dict(o.meta or {}) if isinstance(o.meta, dict) else {}
        payload = str(o.payload or "")
        if o.type == "text" and payload.strip():
            payload = finalize_user_reply(payload, user_text=user_text) or payload
        rows.append(
            {
                "type": o.type,
                "payload": payload,
                "meta": meta,
                "silent_skip": silent and not payload.strip(),
            }
        )
    return rows


async def run_probe(
    *,
    user_id: str,
    text: str,
    group_id: Optional[str],
    channel: str,
    bug_pending: bool,
) -> Dict[str, Any]:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")

    if bug_pending:
        from core.user_bug_report import set_pending

        set_pending(user_id, group_id or user_id, reply_to_message_id=0)

    try:
        from core.turn_observer import install_turn_observer

        install_turn_observer()
    except Exception as e:
        print(f"turn_observer install: {e}", file=sys.stderr)

    from api import orchestrator
    from core.models import Input

    t0 = time.monotonic()
    meta: Dict[str, Any] = {
        "user_id": user_id,
        "channel": channel,
        "group_id": group_id,
        "agent_probe": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    input_data = Input(type="text", payload=text, meta=meta)
    plan = orchestrator.plan(input_data, user_id, group_id)
    _ctx_plan = (plan.steps[0].args or {}).get("context") if plan.steps else {}
    _ff_plan = _ctx_plan.get("facts_flow") if isinstance(_ctx_plan, dict) else {}
    raw_outputs = None
    try:
        from core.models import Output
        from core.user_facts import facts_save_confirm_lane_eligible, try_facts_shortcut_payload

        _recent_plan = (
            _ctx_plan.get("recent_messages") if isinstance(_ctx_plan, dict) else None
        )
        _shortcut = None
        try:
            from core.brain.text_helpers import (
                affirmative_overrides_fact_confirmation,
                looks_like_affirmative_short,
            )
            from core.news_reply import try_affirmative_search_reply_sync

            if looks_like_affirmative_short(text) and affirmative_overrides_fact_confirmation(
                text,
                recent_dialogue=_recent_plan,
                persisted=orchestrator.behavior_store.load(user_id, group_id) or {},
            ):
                _aff = try_affirmative_search_reply_sync(
                    text,
                    persisted=orchestrator.behavior_store.load(user_id, group_id) or {},
                    user_id=str(user_id),
                    recent_dialogue=_recent_plan,
                )
                if _aff and str(_aff).strip():
                    _shortcut = str(_aff).strip()
        except Exception as e:
            print(f"affirmative probe: {e}", file=sys.stderr)
        if _shortcut is None:
            _shortcut = try_facts_shortcut_payload(
                text, _ff_plan, recent_dialogue=_recent_plan
            )
        if _shortcut:
            raw_outputs = [
                Output(type="text", payload=_shortcut, meta={"module": "user_facts", "facts_shortcut": True})
            ]
        elif facts_save_confirm_lane_eligible(_ff_plan):
            raw_outputs = [
                Output(
                    type="text",
                    payload=str(_ff_plan.get("confirmation_prompt") or "").strip(),
                    meta={"module": "user_facts", "confirmation": True},
                )
            ]
    except Exception as e:
        print(f"facts lane probe: {e}", file=sys.stderr)
    if raw_outputs is None:
        raw_outputs = await orchestrator.execute_plan(plan, user_id, group_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    steps = []
    if getattr(plan, "steps", None):
        for s in plan.steps:
            steps.append(
                {
                    "module": getattr(s, "module_name", ""),
                    "args_keys": list((s.args or {}).keys()) if isinstance(s.args, dict) else [],
                }
            )

    out_rows = _outputs_to_dict(raw_outputs, text)
    return {
        "ok": True,
        "user_id": user_id,
        "group_id": group_id,
        "channel": channel,
        "user_text": text,
        "elapsed_ms": elapsed_ms,
        "plan_mode": getattr(plan, "mode", ""),
        "plan_steps": steps,
        "outputs_count": len(out_rows),
        "outputs": out_rows,
        "telegram_messages": [r["payload"] for r in out_rows if r["type"] == "text" and r["payload"].strip()],
        "turns_tail": _tail_turns_jsonl(2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent probe: one turn via orchestrator")
    ap.add_argument("--user-id", required=True, help="Telegram user id (как в логах)")
    ap.add_argument("--text", required=True, help="Текст сообщения пользователя")
    ap.add_argument("--group-id", default=None, help="ID группы или пусто для ЛС")
    ap.add_argument("--channel", default="agent_probe", help="Метка канала в meta")
    ap.add_argument(
        "--bug-pending",
        action="store_true",
        help="Перед ходом выставить pending баг-репорта (как после кнопки «Баг»)",
    )
    ap.add_argument("--json-out", default=None, help="Записать JSON в файл")
    args = ap.parse_args()

    try:
        result = asyncio.run(
            run_probe(
                user_id=str(args.user_id),
                text=args.text,
                group_id=args.group_id,
                channel=args.channel,
                bug_pending=bool(args.bug_pending),
            )
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1

    raw = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(raw, encoding="utf-8")
    print(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
