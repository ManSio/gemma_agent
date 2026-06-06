#!/usr/bin/env python3
"""
Диагностика с seed в behavior — НЕ §9 и НЕ post-deploy gate.

Предпочтительно: scripts/reform_chain_probe.py (orchestrator, без seed).

  python scripts/reform_live_probe.py --limit 4
  python scripts/reform_live_probe.py --case news_world

На сервере (после pull):
  cd /opt/gemma_agent && venv/bin/python3 scripts/reform_live_probe.py --timeout-sec 120
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ.setdefault("GEMMA_PROJECT_ROOT", str(_ROOT))

_PASTE = (
    "Тихановская выступила с обращением к гражданам. "
    "Она заявила о необходимости перемен в стране. " * 12
    + "\n\nЧитайте также на myfin.by подробнее о событиях."
)

_PHILOSOPHY = (
    "Свобода воли и ответственность — не противоречие, а два измерения одного выбора. "
    "Кант отделял явления от вещей в себе; Сартр говорил, что человек осуждён быть свободным. "
    "Как вы это видите?"
)


def _probe_user_id(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    import uuid

    return f"8{uuid.uuid4().hex[:9]}"


def _blob(result: Dict[str, Any]) -> str:
    from core.reform_probe_support import reply_blob

    return reply_blob(result)


def _plan_has_banned_bypass(result: Dict[str, Any]) -> Optional[str]:
    for step in result.get("plan_steps") or []:
        if not isinstance(step, dict):
            continue
        mod = str(step.get("module") or "")
        keys = step.get("args_keys") or []
        if mod == "__fallback__" and "direct_reply" in keys:
            try:
                from core.brain_own_turn import news_digest_search_only_enabled

                if news_digest_search_only_enabled():
                    return None
            except Exception:
                pass
            return "plan_direct_reply_fallback"
    turns = result.get("turns_tail") or []
    if turns:
        last = turns[-1] if isinstance(turns[-1], dict) else {}
        pb = str(last.get("planner_bypass") or "").strip()
        if pb in ("news_direct", "news_item_direct", "weather_direct", "geo_nearby", "affirmative_search"):
            if pb == "news_direct":
                try:
                    from core.brain_own_turn import news_digest_search_only_enabled

                    if news_digest_search_only_enabled():
                        return None
                except Exception:
                    pass
            return f"planner_bypass:{pb}"
    return None


def _seed_probe_persisted(probe_uid: str, seed: Dict[str, Any]) -> None:
    from core.behavior_store import BehaviorStore

    bs = BehaviorStore()
    rec = bs.load(probe_uid, None) or {}
    if isinstance(seed.get("dialogue_state"), dict):
        ds = dict(rec.get("dialogue_state") or {})
        ds.update(seed["dialogue_state"])
        rec["dialogue_state"] = ds
    if isinstance(seed.get("recent_messages"), list):
        rec["recent_messages"] = seed["recent_messages"]
    bs.save(probe_uid, None, rec)


def _digest_seed() -> Dict[str, Any]:
    items = [
        {"title": "UN agency reports global update", "link": "https://example.com/1"},
        {"title": "Economy brief Europe", "link": "https://example.com/2"},
        {"title": "Sports roundup", "link": "https://example.com/3"},
        {
            "title": "Алексей Хлестов: задержание и реакция в Беларуси",
            "link": "https://example.com/4",
        },
    ]
    digest_text = "Новости (дайджест):\n" + "\n".join(
        f"{i}. {it['title']}" for i, it in enumerate(items, start=1)
    )
    return {
        "dialogue_state": {
            "last_news_digest_items": items,
            "last_news_digest_meta": {"query": "новости", "country": "BY"},
        },
        "recent_messages": [
            {"role": "user", "text": "какие новости"},
            {"role": "assistant", "text": digest_text},
        ],
    }


def _affirmative_search_seed() -> Dict[str, Any]:
    return {
        "recent_messages": [
            {"role": "user", "text": "новости про Хлестова"},
            {
                "role": "assistant",
                "text": "Могу поискать в интернете именно по этой фамилии. Продолжить?",
            },
        ],
    }


async def _check_news_digest_pick(probe_uid: str, spec: Dict[str, Any]) -> List[str]:
    from core.behavior_store import BehaviorStore
    from core.news_reply import stash_parsed_digest_from_assistant, try_news_item_reply_sync

    seed = _digest_seed()
    _seed_probe_persisted(probe_uid, seed)
    bs = BehaviorStore()
    rec = bs.load(probe_uid, None) or {}
    digest_body = ""
    for row in seed.get("recent_messages") or []:
        if str(row.get("role") or "") in ("assistant", "bot", "gemma"):
            digest_body = str(row.get("text") or "")
            break
    if digest_body:
        stash_parsed_digest_from_assistant(rec, digest_body)
        bs.save(probe_uid, None, rec)
    try:
        reply = await asyncio.wait_for(
            asyncio.to_thread(
                try_news_item_reply_sync,
                str(spec.get("text") or "4"),
                persisted=rec,
                user_id=probe_uid,
                recent_dialogue=seed.get("recent_messages"),
            ),
            timeout=float(spec.get("max_ms", 150_000)) / 1000.0,
        )
    except asyncio.TimeoutError:
        return ["news_item:timeout"]
    except Exception as e:
        return [f"news_item:{e}"]
    blob = str(reply or "").strip()
    if not blob:
        return ["news_item:empty_reply"]
    errs: List[str] = []
    for pat in spec.get("forbid") or []:
        if re.search(pat, blob, re.IGNORECASE | re.DOTALL):
            errs.append(f"forbid:{pat}")
    need = spec.get("need_any") or []
    if need and not any(re.search(p, blob, re.IGNORECASE) for p in need):
        errs.append(f"need_any_missing:{need}")
    if spec.get("min_len", 0) and len(blob) < int(spec["min_len"]):
        errs.append(f"reply_too_short:{len(blob)}")
    return errs


async def _check_rdel_flow(probe_uid: str) -> List[str]:
    from core.reform_probe_support import run_rdel_acceptance_chain
    from scripts.agent_turn_probe import run_probe

    return await run_rdel_acceptance_chain(
        probe_uid, timeout=90.0, channel="reform_live_probe", run_probe=run_probe
    )


def _cases() -> List[Dict[str, Any]]:
    return [
        {
            "id": "paste_no_country_confirm",
            "kind": "live",
            "text": _PASTE,
            "forbid": [r"запомнить.*стран"],
            "need_any": [],
            "min_len": 60,
            "max_ms": 180_000,
        },
        {
            "id": "news_world",
            "kind": "live",
            "text": "Какие новости в мире",
            "forbid": [r"запомнить.*стран", r"Google News RSS\s*$", r"ленту rss"],
            "need_any": [],
            "min_len": 40,
            "max_ms": 180_000,
        },
        {
            "id": "weather_minsk",
            "kind": "live",
            "text": "погода в Минске",
            "forbid": [r"напишите город", r"укажите город"],
            "need_any": [r"°", r"град", r"Погода", r"прогноз", r"ветер", r"осадк"],
            "min_len": 20,
            "max_ms": 90_000,
        },
        {
            "id": "philosophy_not_weather",
            "kind": "live",
            "text": _PHILOSOPHY,
            "forbid": [r"погода в", r"напишите город", r"°C\s*в Минске"],
            "need_any": [],
            "min_len": 30,
            "max_ms": 120_000,
        },
        {
            "id": "idle_da_not_ack",
            "kind": "live",
            "text": "да",
            "forbid": [r"уже записано", r"ок,\s*уже"],
            "need_any": [],
            "min_len": 0,
            "max_ms": 60_000,
        },
        {
            "id": "news_digest_pick_4",
            "kind": "live",
            "text": "4",
            "seed": _digest_seed,
            "forbid": [r"уже записано", r"пустой ответ"],
            "need_any": [r"Хлестов", r"хлестов"],
            "min_len": 30,
            "max_ms": 150_000,
        },
        {
            "id": "affirmative_search_da",
            "kind": "live",
            "text": "да",
            "seed": _affirmative_search_seed,
            "forbid": [r"уже записано", r"ок,\s*уже"],
            "need_any": [r"поиск", r"найден", r"Хлестов", r"хлестов", r"http"],
            "min_len": 20,
            "max_ms": 180_000,
        },
        {
            "id": "rdel_1",
            "kind": "rdel",
        },
    ]


def _validate_live(result: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not result.get("ok"):
        return [str(result.get("error") or "probe_failed")]
    bypass = _plan_has_banned_bypass(result)
    if bypass:
        errs.append(bypass)
    blob = _blob(result).strip()
    if spec.get("min_len", 0) and len(blob) < int(spec["min_len"]):
        errs.append(f"reply_too_short:{len(blob)}")
    for pat in spec.get("forbid") or []:
        if re.search(pat, blob, re.IGNORECASE | re.DOTALL):
            errs.append(f"forbid:{pat}")
    need = spec.get("need_any") or []
    if need and not any(re.search(p, blob, re.IGNORECASE) for p in need):
        errs.append(f"need_any_missing:{need}")
    return errs


async def _run_live_case(
    user_id: str,
    spec: Dict[str, Any],
    *,
    timeout_sec: float,
) -> Dict[str, Any]:
    from scripts.agent_turn_probe import run_probe

    uid = f"{user_id}.reform.{spec['id'][:12]}"
    seed_fn = spec.get("seed")
    if callable(seed_fn):
        _seed_probe_persisted(uid, seed_fn())
    try:
        result = await asyncio.wait_for(
            run_probe(
                user_id=uid,
                text=str(spec["text"]),
                group_id=None,
                channel="reform_live_probe",
                bug_pending=False,
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"timeout_{int(timeout_sec)}s", "id": spec["id"]}
    except Exception as e:
        return {"ok": False, "error": str(e), "id": spec["id"]}
    errs = _validate_live(result, spec)
    return {
        "id": spec["id"],
        "ok": not errs,
        "errors": errs,
        "elapsed_ms": result.get("elapsed_ms"),
        "reply_preview": _blob(result)[:240],
        "plan_steps": result.get("plan_steps"),
    }


async def _run_all(
    *,
    user_id: str,
    case_ids: Optional[List[str]],
    limit: int,
    timeout_sec: float,
) -> Dict[str, Any]:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    os.environ.setdefault("BRAIN_OWN_TURN_ENABLED", "true")
    for k in ("NEWS", "WEATHER", "GEO_NEARBY", "AFFIRMATIVE_SEARCH"):
        os.environ.setdefault(f"BRAIN_OWN_TURN_ALLOW_{k}", "false")

    specs = _cases()
    if case_ids:
        want = set(case_ids)
        specs = [s for s in specs if s["id"] in want]
    if limit > 0:
        specs = specs[:limit]

    rows: List[Dict[str, Any]] = []
    failed = 0
    t0 = time.monotonic()
    for spec in specs:
        print(f"… {spec['id']}", flush=True)
        probe_uid = f"{user_id}.reform.{spec['id'][:12]}"
        if spec.get("kind") == "rdel":
            errs = await _check_rdel_flow(probe_uid)
            row = {"id": spec["id"], "ok": not errs, "errors": errs, "kind": "rdel"}
        elif spec.get("kind") == "news_item":
            errs = await _check_news_digest_pick(probe_uid, spec)
            row = {"id": spec["id"], "ok": not errs, "errors": errs, "kind": "news_item"}
        else:
            cap = min(timeout_sec, float(spec.get("max_ms", 120_000)) / 1000.0)
            row = await _run_live_case(user_id, spec, timeout_sec=cap)
            row["kind"] = "live"
        if not row.get("ok"):
            failed += 1
        rows.append(row)
        print(f"  {'OK' if row.get('ok') else 'FAIL'} {spec['id']}", flush=True)
        if row.get("errors"):
            for e in row["errors"]:
                print(f"    {e}", flush=True)

    from core.reform_probe_support import cleanup_probe_behavior

    cleanup_probe_behavior(user_id)
    for spec in specs:
        cleanup_probe_behavior(f"{user_id}.reform.{spec['id'][:12]}")

    return {
        "user_id": user_id,
        "synthetic_seed": True,
        "not_telegram_s9": True,
        "total": len(rows),
        "passed": len(rows) - failed,
        "failed": failed,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "cases": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="", help="Probe user (default POST_DEPLOY_PROBE_USER_ID)")
    ap.add_argument("--case", action="append", default=[], help="Только эти id кейсов")
    ap.add_argument("--limit", type=int, default=0, help="Первые N кейсов")
    ap.add_argument("--timeout-sec", type=float, default=120.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    uid = _probe_user_id(args.user_id)
    doc = asyncio.run(
        _run_all(
            user_id=uid,
            case_ids=args.case or None,
            limit=args.limit,
            timeout_sec=args.timeout_sec,
        )
    )
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        print(f"\n{doc['passed']}/{doc['total']} passed in {doc['elapsed_ms']}ms")
    return 0 if doc.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
