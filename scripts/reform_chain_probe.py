#!/usr/bin/env python3
"""
Диагностика цепочек через orchestrator, БЕЗ seed в behavior.

Не §9 и не deploy-smoke: behavior_store + probe ≠ Telegram (Lock, polling, Markdown).
Продуктовая приёмка — только live runbook в REFORM_S9_ACCEPTANCE_TRACKER_RU.md.

  python scripts/reform_chain_probe.py
  python scripts/reform_chain_probe.py --user-id 900000001
  python scripts/reform_chain_probe.py --quick   # только route-critical (без LLM)

На сервере:
  cd /opt/gemma_agent && venv/bin/python3 scripts/reform_chain_probe.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

_LOG = sys.stderr
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ.setdefault("GEMMA_PROJECT_ROOT", str(_ROOT))

_PHILOSOPHY = (
    "Свобода воли и ответственность — не противоречие, а два измерения одного выбора. "
    "Кант отделял явления от вещей в себе; Сартр говорил, что человек осуждён быть свободным. "
    "В современной нейронауке спорят, иллюзия ли свобода воли или эмерджентное свойство. "
    "Как вы это видите?"
)


def _probe_user_id(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    return f"8{uuid.uuid4().hex[:9]}"


def _reply_blob(result: Dict[str, Any]) -> str:
    from core.reform_probe_support import reply_blob

    return reply_blob(result)


async def _turn(user_id: str, text: str, *, timeout: float) -> Dict[str, Any]:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from scripts.agent_turn_probe import run_probe

    return await asyncio.wait_for(
        run_probe(
            user_id=user_id,
            text=text,
            group_id=None,
            channel="reform_chain_probe",
            bug_pending=False,
        ),
        timeout=timeout,
    )


def _load_rec(user_id: str) -> Dict[str, Any]:
    from core.behavior_store import BehaviorStore

    return BehaviorStore().load(user_id, None) or {}


async def _chain_news_then_four(uid: str, timeout: float) -> List[str]:
    errs: List[str] = []
    r1 = await _turn(uid, "Какие новости в мире", timeout=timeout)
    b1 = _reply_blob(r1)
    if len(b1) < 80:
        errs.append(f"news_turn1_short:{len(b1)}")
    if re.search(r"запомнить.*стран", b1, re.I):
        errs.append("news_turn1_country_confirm")
    rec = _load_rec(uid)
    ds = rec.get("dialogue_state") if isinstance(rec.get("dialogue_state"), dict) else {}
    items = ds.get("last_news_digest_items")
    has_numbered = bool(re.search(r"(?m)^\d+\.\s+\S", b1))
    has_items = isinstance(items, list) and len(items) >= 2
    if not has_numbered and not has_items:
        errs.append("news_turn1_no_digest_state")
    await asyncio.sleep(0.5)
    r2 = await _turn(uid, "4", timeout=min(timeout, 150.0))
    b2 = _reply_blob(r2)
    if len(b2) < 40:
        errs.append(f"news_pick4_short:{len(b2)}")
    if "не вижу свежего списка" in b2.lower():
        errs.append("news_pick4_no_digest")
    if re.search(r"пустой ответ", b2, re.I):
        errs.append("news_pick4_empty_guard")
    return errs


async def _chain_affirmative_search(uid: str, timeout: float) -> List[str]:
    errs: List[str] = []
    r1 = await _turn(
        uid,
        "Кратко: что известно про Алексея Хлестова в новостях?",
        timeout=timeout,
    )
    b1 = _reply_blob(r1)
    if len(b1) < 40:
        errs.append(f"aff_turn1_short:{len(b1)}")
    r2 = await _turn(uid, "да", timeout=min(timeout, 120.0))
    b2 = _reply_blob(r2)
    if re.search(r"уже записано", b2, re.I):
        errs.append("aff_turn2_idle_ack")
    if len(b2) < 25 and not re.search(r"поиск|найден|http|хлестов", b2, re.I):
        errs.append(f"aff_turn2_weak:{b2[:80]}")
    return errs


async def _chain_paste(uid: str, timeout: float) -> List[str]:
    errs: List[str] = []
    paste = (
        "Тихановская выступила с обращением к гражданам. "
        "Она заявила о необходимости перемен. " * 14
        + "\n\nЧитайте также на myfin.by."
    )
    r = await _turn(uid, paste, timeout=timeout)
    b = _reply_blob(r)
    if len(b) < 60:
        errs.append(f"paste_short:{len(b)}")
    if re.search(r"запомнить.*стран", b, re.I):
        errs.append("paste_country_confirm")
    return errs


async def _chain_weather_minsk(uid: str, timeout: float) -> List[str]:
    from core.reform_probe_support import validate_weather_reply

    r = await _turn(uid, "погода в Минске", timeout=min(timeout, 90.0))
    return validate_weather_reply(_reply_blob(r))


async def _chain_philosophy(uid: str, timeout: float) -> List[str]:
    from core.reform_probe_support import validate_philosophy_reply

    r = await _turn(uid, _PHILOSOPHY, timeout=min(timeout, 120.0))
    return validate_philosophy_reply(_reply_blob(r))


async def _chain_correction(uid: str, timeout: float) -> List[str]:
    from core.reform_probe_support import validate_pending_correction

    await _turn(uid, "Расскажи про квантовую запутанность в двух предложениях", timeout=min(timeout, 120.0))
    await _turn(uid, "не так — отвечай короче, одним абзацем", timeout=min(timeout, 120.0))
    return validate_pending_correction(_load_rec(uid))


async def _chain_rdel(uid: str, timeout: float) -> List[str]:
    from core.reform_probe_support import run_rdel_acceptance_chain
    from scripts.agent_turn_probe import run_probe

    return await run_rdel_acceptance_chain(
        uid, timeout=min(timeout, 90.0), channel="reform_chain_probe", run_probe=run_probe
    )


_ALL_CHAINS: List[tuple[str, Callable]] = [
    ("news_then_4", _chain_news_then_four),
    ("affirmative_search", _chain_affirmative_search),
    ("paste_article", _chain_paste),
    ("weather_minsk", _chain_weather_minsk),
    ("philosophy_not_weather", _chain_philosophy),
    ("user_correction", _chain_correction),
    ("rdel_after_radd", _chain_rdel),
]

_QUICK_CHAIN_IDS = frozenset({"rdel_after_radd"})


async def _run_chains(
    uid: str,
    timeout: float,
    *,
    quick: bool = False,
) -> Dict[str, Any]:
    os.environ.setdefault("BRAIN_OWN_TURN_ENABLED", "true")
    os.environ.setdefault("BRAIN_NEWS_ITEM_REPLY_ENABLED", "true")
    chains = [
        (cid, fn)
        for cid, fn in _ALL_CHAINS
        if not quick or cid in _QUICK_CHAIN_IDS
    ]
    rows: List[Dict[str, Any]] = []
    failed = 0
    t0 = time.monotonic()
    for cid, fn in chains:
        print(f"chain {cid} uid={uid} …", file=_LOG, flush=True)
        try:
            errs = await fn(uid, timeout)
        except asyncio.TimeoutError:
            errs = [f"timeout:{int(timeout)}s"]
        except Exception as e:
            errs = [f"exception:{e}"]
        ok = not errs
        if not ok:
            failed += 1
        rows.append({"id": cid, "ok": ok, "errors": errs})
        print(f"  {'OK' if ok else 'FAIL'} {cid}", file=_LOG, flush=True)
        for e in errs:
            print(f"    {e}", file=_LOG, flush=True)
    from core.reform_probe_support import cleanup_probe_behavior

    cleanup_probe_behavior(uid)
    return {
        "user_id": uid,
        "synthetic_seed": False,
        "quick_mode": quick,
        "total": len(rows),
        "passed": len(rows) - failed,
        "failed": failed,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "chains": rows,
        "note": "orchestrator chains — not Telegram §9; see REFORM_S9_ACCEPTANCE_TRACKER",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="", help="По умолчанию случайный 8xxxxxxxxx")
    ap.add_argument("--timeout-sec", type=float, default=200.0)
    ap.add_argument("--quick", action="store_true", help="Только rdel (без LLM)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    uid = _probe_user_id(args.user_id)
    doc = asyncio.run(_run_chains(uid, args.timeout_sec, quick=args.quick))
    if args.json:
        sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        mode = "quick" if args.quick else "full"
        print(f"\n{doc['passed']}/{doc['total']} chains ({mode}, no seed)", file=_LOG)
    return 0 if doc.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
