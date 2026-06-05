#!/usr/bin/env python3
"""
Пакетный прогон корпуса (probe): один раз грузит orchestrator, пишет отчёт + уроки на провалы.

  python scripts/build_test_corpus.py --target 1000
  python scripts/agent_test_runner.py --corpus data/testing/corpus.jsonl --tier smoke
  python scripts/agent_test_runner.py --corpus data/testing/corpus.jsonl --limit 50 --resume
  python scripts/agent_telegram_client.py --suite tasks   # живой Telegram

tier smoke = только regression (базовые + route_only + цепочки dialog_turns)
tier archive = regression + все arch_*
tier full = весь corpus
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ.setdefault("GEMMA_PROJECT_ROOT", str(_ROOT))


def _load_corpus(path: Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            cases.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return cases


def _filter_tier(cases: List[Dict[str, Any]], tier: str) -> List[Dict[str, Any]]:
    if tier == "smoke":
        return [c for c in cases if c.get("source") == "regression"]
    if tier == "archive":
        return [c for c in cases if c.get("source") in ("regression", "archive")]
    return cases


def _filter_tags(cases: List[Dict[str, Any]], tags: List[str]) -> List[Dict[str, Any]]:
    if not tags:
        return cases
    want = {t.strip() for t in tags if t.strip()}
    out: List[Dict[str, Any]] = []
    for c in cases:
        ct = {str(x) for x in (c.get("tags") or [])}
        if want & ct:
            out.append(c)
    return out


def _load_done_ids(report_path: Path) -> Set[str]:
    if not report_path.is_file():
        return set()
    done: Set[str] = set()
    for ln in report_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            o = json.loads(ln)
            cid = str(o.get("id") or "")
            if cid:
                done.add(cid)
        except json.JSONDecodeError:
            continue
    return done


def _dialog_turn_messages(case: Dict[str, Any]) -> List[str]:
    """Сообщения пользователя по кейсу: поле dialog_turns (2+) или одно text."""
    raw = case.get("dialog_turns")
    if isinstance(raw, list):
        msgs = [str(x).strip() for x in raw if str(x).strip()]
        if msgs:
            return msgs
    t = str(case.get("text") or "").strip()
    return [t] if t else []


async def _run_batch(
    cases: List[Dict[str, Any]],
    *,
    report_path: Path,
    failures_lessons_path: Path,
    include_image_gen: bool = False,
) -> Dict[str, Any]:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from api import orchestrator
    from core.agent_test_validators import validate_reply
    from core.models import Input
    from core.turn_chain_audit import build_turn_chain, trajectory_summary

    if cases and cases[0].get("bug_pending"):
        pass  # per-case below

    passed = 0
    failed = 0
    skipped = 0
    t0 = time.monotonic()

    report_path.parent.mkdir(parents=True, exist_ok=True)
    failures_lessons_path.parent.mkdir(parents=True, exist_ok=True)

    with report_path.open("a", encoding="utf-8") as rep_f, failures_lessons_path.open(
        "a", encoding="utf-8"
    ) as les_f:
        for i, case in enumerate(cases):
            cid = str(case.get("id") or f"case_{i}")
            skip_reason = _should_skip_case(case, include_image_gen=include_image_gen)
            if skip_reason:
                skipped += 1
                print(f"[{i+1}/{len(cases)}] {cid} SKIP ({skip_reason})", flush=True)
                continue
            msgs = _dialog_turn_messages(case)
            if not msgs:
                skipped += 1
                print(f"[{i+1}/{len(cases)}] {cid} SKIP (empty text/dialog_turns)", flush=True)
                continue
            last_user = msgs[-1]
            joined_user = " | ".join(m[:160] for m in msgs)
            uid = _isolated_user_id(case)
            print(f"[{i+1}/{len(cases)}] {cid} uid={uid} …", flush=True)

            if case.get("route_only"):
                row_ts = datetime.now(timezone.utc).isoformat()
                meta_pre = {
                    "user_id": uid,
                    "channel": "agent_test",
                    "agent_test_id": cid,
                    "agent_test_isolate": True,
                    "timestamp": row_ts,
                }
                if len(msgs) > 1:
                    for msg in msgs[:-1]:
                        inp_pre = Input(type="text", payload=msg, meta=meta_pre)
                        plan_pre = orchestrator.plan(inp_pre, uid, None)
                        await orchestrator.execute_plan(plan_pre, uid, None)
                errs = validate_reply("", last_user, case)
                row = {
                    "ts": row_ts,
                    "id": cid,
                    "source": case.get("source"),
                    "user_text": joined_user[:400],
                    "tags": case.get("tags") or [],
                    "route_only": True,
                    "trajectory": trajectory_summary(uid, last_user),
                    "elapsed_ms": 0,
                    "pass": not errs,
                    "errors": errs,
                }
                if errs:
                    failed += 1
                    print(f"  FAIL {errs}", flush=True)
                else:
                    passed += 1
                    print("  PASS (route_only)", flush=True)
                rep_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                rep_f.flush()
                continue

            if case.get("bug_pending"):
                from core.user_bug_report import set_pending

                set_pending(uid, uid, reply_to_message_id=0)

            row: Dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "id": cid,
                "source": case.get("source"),
                "user_text": joined_user[:400],
                "tags": case.get("tags") or [],
            }
            try:
                t1 = time.monotonic()
                t0_unix = time.time()
                meta = {
                    "user_id": uid,
                    "channel": "agent_test",
                    "agent_test_id": cid,
                    "agent_test_isolate": True,
                    "timestamp": row["ts"],
                }
                row["probe_user_id"] = uid
                for msg in msgs[:-1]:
                    inp_pre = Input(type="text", payload=msg, meta=meta)
                    plan_pre = orchestrator.plan(inp_pre, uid, None)
                    await orchestrator.execute_plan(plan_pre, uid, None)
                inp = Input(type="text", payload=last_user, meta=meta)
                plan = orchestrator.plan(inp, uid, None)
                outputs = await orchestrator.execute_plan(plan, uid, None)
                elapsed_ms = int((time.monotonic() - t1) * 1000)
                texts: List[str] = []
                for o in outputs or []:
                    if getattr(o, "type", "") == "text" and str(getattr(o, "payload", "") or "").strip():
                        texts.append(str(o.payload).strip())
                reply = "\n".join(texts) if texts else ""
                chain = build_turn_chain(
                    case_id=cid,
                    user_text=last_user,
                    user_id=uid,
                    reply=reply,
                    case=case,
                    plan=plan,
                    elapsed_ms=elapsed_ms,
                    t0=t0_unix,
                )
                row["chain"] = chain
                row["trajectory"] = chain.get("trajectory") or trajectory_summary(uid, last_user, plan)
                errs = list(chain.get("errors") or chain.get("validators") or [])
                row["elapsed_ms"] = elapsed_ms
                row["reply_preview"] = reply[:400]
                row["outputs_count"] = len(texts)
                row["pass"] = bool(chain.get("pass"))
                row["errors"] = errs
                if errs:
                    failed += 1
                    lesson = {
                        "ts": row["ts"],
                        "test_id": cid,
                        "user_text": joined_user[:300],
                        "errors": errs,
                        "reply_preview": reply[:500],
                        "hint": _failure_hint(errs, case, last_user=last_user),
                    }
                    les_f.write(json.dumps(lesson, ensure_ascii=False) + "\n")
                    print(f"  FAIL {errs}", flush=True)
                else:
                    passed += 1
                    print("  PASS", flush=True)
            except Exception as e:
                failed += 1
                row["pass"] = False
                row["errors"] = [f"exception:{type(e).__name__}"]
                row["error_detail"] = str(e)[:500]
                print(f"  ERROR {e}", flush=True)
            rep_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rep_f.flush()

    total_ms = int((time.monotonic() - t0) * 1000)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": len(cases),
        "elapsed_ms": total_ms,
        "report": str(report_path),
        "lessons": str(failures_lessons_path),
    }


def _wants_image_generation(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.lower().startswith("/imagine"):
        return True
    try:
        from core.image_gen_nl import prose_wants_image_generation

        return bool(prose_wants_image_generation(t))
    except Exception:
        return "изображен" in t.lower() and "сгенер" in t.lower()


def _should_skip_case(case: Dict[str, Any], *, include_image_gen: bool) -> Optional[str]:
    """Платная image_gen — один smoke-кейс только с --include-image-gen."""
    if case.get("allow_image_gen") or case.get("id") == "reg_image_gen":
        return None if include_image_gen else "skip_image_gen_paid_smoke"
    for msg in _dialog_turn_messages(case):
        if _wants_image_generation(msg):
            return "skip_image_gen_paid"
    if "image_gen" in (case.get("tags") or []):
        return "skip_image_gen_tag"
    return None


def _isolated_user_id(case: Dict[str, Any]) -> str:
    """Отдельный behavior_store на кейс — без «текущего» диалога между тестами."""
    if case.get("isolate") is False:
        from probe_user_id import default_probe_user_id

        return str(case.get("user_id") or default_probe_user_id() or "900000001")
    cid = str(case.get("id") or case.get("text") or "x")
    h = int(hashlib.sha256(cid.encode("utf-8")).hexdigest()[:12], 16) % 900_000_000
    return f"9{h:09d}"


def _failure_hint(errs: List[str], case: Dict[str, Any], *, last_user: str = "") -> str:
    if any("leak" in e for e in errs):
        return "Утечка промпта/XML — усилить response_finalize и scenario_engine."
    if "fallback" in str(errs):
        return "Fallback вместо ответа — проверить scenario empty_output и brain pipeline."
    if "regex" in str(errs) or "missing" in str(errs):
        u = (last_user or case.get("text") or "")[:80]
        return f"Содержание не соответствует задаче: {u}"
    return "См. errors и turns.jsonl для этого хода."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/testing/corpus.jsonl")
    ap.add_argument("--tier", choices=["smoke", "archive", "full"], default="smoke")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--route-only", action="store_true", help="Только кейсы route_only (без LLM)")
    ap.add_argument("--llm-only", action="store_true", help="Только кейсы с LLM (без route_only)")
    ap.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Фильтр по тегу корпуса (можно несколько --tag reform_20260525)",
    )
    ap.add_argument("--include-image-gen", action="store_true", help="Включить платный image smoke")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--report", default="data/testing/reports/latest.jsonl")
    ap.add_argument("--lessons", default="data/runtime/agent_test_lessons.jsonl")
    args = ap.parse_args()

    corpus_path = _ROOT / args.corpus
    if not corpus_path.is_file():
        print(f"Нет корпуса: {corpus_path}. Запустите build_test_corpus.py", file=sys.stderr)
        return 2

    cases = _filter_tier(_load_corpus(corpus_path), args.tier)
    cases = _filter_tags(cases, args.tag)
    if args.route_only:
        cases = [c for c in cases if c.get("route_only")]
    elif args.llm_only:
        cases = [c for c in cases if not c.get("route_only")]
    report_path = _ROOT / args.report
    if args.resume:
        done = _load_done_ids(report_path)
        cases = [c for c in cases if str(c.get("id")) not in done]
    if args.limit > 0:
        cases = cases[: args.limit]

    if not cases:
        print("Нечего гонять (пустой отбор или всё уже в resume).")
        return 0

    print(f"Запуск {len(cases)} кейсов tier={args.tier} …", flush=True)
    summary = asyncio.run(
        _run_batch(
            cases,
            report_path=report_path,
            failures_lessons_path=_ROOT / args.lessons,
            include_image_gen=bool(args.include_image_gen),
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
