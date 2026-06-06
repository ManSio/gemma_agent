#!/usr/bin/env python3
"""
Осмысленные пробы чата через API (ops_probe) — без «мусора» из smoke.

  python scripts/agent_chat_probe_curated.py
  python scripts/agent_chat_probe_curated.py --json-out data/benchmarks/chat_probe_curated.json

Env: API_TOKEN, API_PORT/API_HOST или AGENT_PROBE_API_URL, AGENT_PROBE_HTTP_MIN_INTERVAL_SEC.
User: POST_DEPLOY_PROBE_USER_ID / OWNER_TELEGRAM_ID (каждый кейс — свой isolated user_id).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from probe_user_id import default_probe_user_id  # noqa: E402

_LEAK_RE = re.compile(
    r"(теперь ответь пользователю|tool_call:|<rule\s+name=|priority=\"override)",
    re.IGNORECASE,
)
_FALLBACK_RE = re.compile(r"не удалось сформировать нормальный ответ", re.IGNORECASE)

# §9 BRAIN_CENTRIC + TASK_SUITE + PRODUCT_FINISH — только осмысленные реплики.
CASES: List[Dict[str, Any]] = [
    {
        "id": "chitchat_hi",
        "messages": ["привет"],
        "expect_any": ["привет", "здравств", "дела", "помочь"],
    },
    {
        "id": "math_17x23",
        "messages": ["сколько будет 17*23+5"],
        "expect_any": ["396"],
    },
    {
        "id": "code_factorial",
        "messages": ["напиши функцию на Python для факториала"],
        "expect_any": ["def", "factorial", "факториал"],
    },
    {
        "id": "explain_earth",
        "messages": ["Почему земля круглая"],
        "expect_any": ["земл", "гравит", "сфер", "кругл", "форма"],
        "expect_not": ["^(ок|ok)\\s*$"],
    },
    {
        "id": "translate_en",
        "messages": ['переведи на английский: "доброе утро"'],
        "expect_any": ["good morning", "morning"],
    },
    {
        "id": "geo_capital",
        "messages": ["столица Минска"],
        "expect_any": ["минск", "беларус"],
    },
    {
        "id": "weather_minsk",
        "messages": ["погода в Минске"],
        "expect_any": ["°", "град", "температур", "погод", "облач"],
    },
    {
        "id": "news_world",
        "messages": ["Какие новости в мире"],
        "expect_any": ["новост", "заголов", "1."],
        "expect_not": ["не удалось"],
    },
    {
        "id": "brief_ok",
        "messages": ["скажи только: ок"],
        "expect_regex": r"^(ок|ok)\s*[\.\!]?\s*$",
    },
    {
        "id": "chain_math_context",
        "messages": [
            "сколько будет 11*13, ответь только числом",
            "к тому числу прибавь 7, снова только число",
        ],
        "expect_any": ["150"],
    },
    {
        "id": "chain_pivot_physics",
        "messages": [
            "коротко: чем диверсифицировать портфель акций и облигаций на 5 лет?",
            "стоп, другая тема: почему небо голубое — одним абзацем",
        ],
        "expect_any": ["небо", "голуб", "рассеив", "солнеч", "атмосф"],
        "expect_not": ["портфел", "облигац"],
    },
]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except Exception:
        pass


def _api_base() -> str:
    return (
        os.getenv("AGENT_PROBE_API_URL")
        or f"http://{os.getenv('API_HOST', '127.0.0.1')}:{os.getenv('API_PORT', '8000')}"
    ).rstrip("/")


def _api_token() -> str:
    return (os.getenv("API_TOKEN") or "").strip()


def _min_interval() -> float:
    raw = (os.getenv("AGENT_PROBE_HTTP_MIN_INTERVAL_SEC") or "14").strip()
    try:
        return max(12.0, float(raw))
    except ValueError:
        return 14.0


_last_call = 0.0


def _throttle() -> None:
    global _last_call
    gap = _min_interval()
    wait = gap - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _isolated_uid(case_id: str, base: str) -> str:
    h = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12], 16) % 900_000_000
    return f"9{h:09d}"


def _api_probe(user_id: str, text: str, *, timeout: float) -> Dict[str, Any]:
    _throttle()
    body = {
        "user_id": user_id,
        "message": text,
        "channel": "chat_probe_curated",
        "group_id": None,
    }
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    token = _api_token()
    req = urllib.request.Request(
        f"{_api_base()}/api/v1/ops/probe",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_reply(data: Dict[str, Any]) -> str:
    trace = data.get("trace") if isinstance(data.get("trace"), dict) else {}
    return str(trace.get("assistant_text") or data.get("reply") or "").strip()


def _check_reply(reply: str, case: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not reply:
        errs.append("empty_reply")
    if _FALLBACK_RE.search(reply):
        errs.append("fallback")
    if _LEAK_RE.search(reply):
        errs.append("leak")
    for pat in case.get("expect_not") or []:
        if re.search(pat, reply, re.IGNORECASE | re.MULTILINE):
            errs.append(f"forbidden:{pat[:30]}")
    expect_re = case.get("expect_regex")
    if expect_re and not re.search(expect_re, reply, re.IGNORECASE | re.MULTILINE):
        errs.append(f"regex_miss")
    need = case.get("expect_any") or []
    if need:
        low = reply.lower()
        if not any(str(w).lower() in low for w in need):
            errs.append(f"missing:{','.join(need[:3])}")
    return errs


def _run_case(case: Dict[str, Any], *, base_uid: str, timeout: float) -> Dict[str, Any]:
    cid = str(case["id"])
    uid = _isolated_uid(cid, base_uid)
    msgs = [str(m).strip() for m in (case.get("messages") or []) if str(m).strip()]
    row: Dict[str, Any] = {
        "id": cid,
        "user_id": uid,
        "messages": msgs,
        "turns": [],
        "ok": False,
        "errors": [],
    }
    last_reply = ""
    try:
        for i, text in enumerate(msgs):
            t0 = time.monotonic()
            data = _api_probe(uid, text, timeout=timeout)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            last_reply = _extract_reply(data)
            issues = []
            trace = data.get("trace") if isinstance(data.get("trace"), dict) else {}
            if isinstance(trace.get("issues"), list):
                issues = trace["issues"]
            row["turns"].append(
                {
                    "n": i + 1,
                    "user": text[:200],
                    "reply_preview": last_reply[:400],
                    "elapsed_ms": elapsed_ms,
                    "trace_issues": issues,
                }
            )
        row["errors"] = _check_reply(last_reply, case)
        row["ok"] = not row["errors"]
        row["last_reply_preview"] = last_reply[:500]
    except urllib.error.HTTPError as e:
        row["errors"] = [f"http_{e.code}"]
        row["http_body"] = e.read().decode("utf-8", errors="replace")[:500]
    except Exception as e:
        row["errors"] = [f"exception:{type(e).__name__}"]
        row["error_detail"] = str(e)[:300]
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=float(os.getenv("AGENT_PROBE_HTTP_TIMEOUT_SEC", "300")))
    ap.add_argument("--json-out", default="", help="путь к отчёту JSON")
    ap.add_argument("--user-id", default="", help="база для isolated uid (не обязательна)")
    args = ap.parse_args()

    _load_dotenv()
    base = (args.user_id or default_probe_user_id() or "900000001").strip()
    token = _api_token()
    if not token:
        print("Задайте API_TOKEN в .env", file=sys.stderr)
        return 2

    report: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "api_base": _api_base(),
        "cases_total": len(CASES),
        "results": [],
    }
    passed = 0
    for case in CASES:
        print(f"… {case['id']}", flush=True)
        row = _run_case(case, base_uid=base, timeout=args.timeout)
        report["results"].append(row)
        mark = "OK" if row.get("ok") else "FAIL"
        print(f"  {mark}  {row.get('errors') or []}")
        if row.get("ok"):
            passed += 1

    report["passed"] = passed
    report["failed"] = len(CASES) - passed
    report["status"] = "PASS" if passed == len(CASES) else "FAIL"

    out = args.json_out.strip()
    if out:
        path = Path(out)
        if not path.is_absolute():
            path = _ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {path}")

    print(f"\nИтого: {passed}/{len(CASES)} PASS")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
