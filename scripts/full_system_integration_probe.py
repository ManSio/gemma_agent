#!/usr/bin/env python3
"""
Полный интеграционный прогон: pytest route + agent_test_runner smoke + API ops_probe + Telegram suites.

  cd /opt/gemma_agent
  PROBE_GAP_SEC=15 python scripts/full_system_integration_probe.py

Отчёт: data/benchmarks/full_system_report_<ts>.json + .md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
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
os.chdir(_ROOT)
os.environ.setdefault("GEMMA_PROJECT_ROOT", str(_ROOT))

from probe_user_id import default_probe_user_id  # noqa: E402

# Модули / дорожки — дополнение к suites tasks + master_plan_v1
MODULE_CASES: List[Dict[str, Any]] = [
    {"id": "mod_chitchat", "text": "привет", "expect_any": ["привет", "здравств", "дела"]},
    {"id": "mod_news", "text": "Что нового в мире", "expect_any": ["новост", "заголов", "1."]},
    {"id": "mod_social", "text": "как найти друзей", "expect_any": ["друз", "интерес", "клуб"]},
    {"id": "mod_translate", "text": 'переведи на английский: "доброе утро"', "expect_any": ["good morning", "morning"]},
    {"id": "mod_memory", "text": "О чём мы говорили в последних сообщениях?", "expect_any": []},
    {"id": "mod_explain", "text": "Почему небо голубое", "expect_any": ["небо", "свет", "рассе"]},
]

API_CASES: List[Dict[str, Any]] = [
    {"id": "api_ping", "endpoint": "ping"},
    {"id": "api_chat_hi", "endpoint": "chat", "text": "привет"},
    {"id": "api_probe_math", "endpoint": "ops_probe", "text": "2+2"},
    {"id": "api_probe_news", "endpoint": "ops_probe", "text": "кратко новости технологий"},
    {"id": "api_probe_code", "endpoint": "ops_probe", "text": "def hello(): pass"},
]


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run(cmd: List[str], *, timeout: float = 600) -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "cmd": cmd,
            "exit_code": p.returncode,
            "stdout": (p.stdout or "")[-8000:],
            "stderr": (p.stderr or "")[-4000:],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "ok": p.returncode == 0,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "cmd": cmd,
            "exit_code": -1,
            "stdout": (e.stdout or "")[-4000:] if e.stdout else "",
            "stderr": (e.stderr or "")[-2000:] if e.stderr else "timeout",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "ok": False,
        }


def _load_dotenv() -> None:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")


def _api_base() -> str:
    return (os.getenv("AGENT_PROBE_API_URL") or f"http://{os.getenv('API_HOST', '127.0.0.1')}:{os.getenv('API_PORT', '8000')}").rstrip("/")


def _api_token() -> str:
    return (os.getenv("API_TOKEN") or "").strip()


def _api_min_interval() -> float:
    raw = (os.getenv("AGENT_PROBE_HTTP_MIN_INTERVAL_SEC") or "14").strip()
    try:
        return max(12.0, float(raw))
    except ValueError:
        return 14.0


_last_api = 0.0


def _api_throttle() -> None:
    global _last_api
    gap = _api_min_interval()
    wait = gap - (time.monotonic() - _last_api)
    if wait > 0:
        time.sleep(wait)
    _last_api = time.monotonic()


def _api_get(path: str, token: str, timeout: float = 30) -> Dict[str, Any]:
    _api_throttle()
    req = urllib.request.Request(
        f"{_api_base()}{path}",
        headers={"X-API-Token": token, "Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        try:
            return {"ok": True, "status": resp.status, "json": json.loads(body)}
        except json.JSONDecodeError:
            return {"ok": True, "status": resp.status, "text": body[:2000]}


def _api_post(path: str, token: str, body: Dict[str, Any], timeout: float = 300) -> Dict[str, Any]:
    _api_throttle()
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{_api_base()}{path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "json": json.loads(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "error": raw[:1500]}


def _check_text(texts: List[str], expect_any: List[str]) -> bool:
    if not expect_any:
        return bool(texts and any(len(t.strip()) > 15 for t in texts))
    blob = " ".join(texts).lower()
    return any(x.lower() in blob for x in expect_any)


def _run_pytest_bundle(py: str) -> Dict[str, Any]:
    try:
        subprocess.run([py, "-m", "pytest", "--version"], capture_output=True, check=True, timeout=15)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {"ok": True, "skipped": True, "reason": "pytest not available on host"}
    tests = [
        "tests/test_profile_route_guard.py",
        "tests/test_incident_route_regression.py",
        "tests/test_turn_quality_loop.py",
        "tests/test_product_behavior.py",
    ]
    return _run([py, "-m", "pytest", "-q", *tests], timeout=180)


def _run_smoke_runner(py: str) -> Dict[str, Any]:
    corpus = _ROOT / "data" / "testing" / "corpus.jsonl"
    if not corpus.is_file():
        return {"ok": False, "skipped": True, "reason": "no corpus.jsonl"}
    rep = _ROOT / "data" / "testing" / "reports" / f"full_system_{_now_slug()}.jsonl"
    return _run(
        [
            py,
            "scripts/agent_test_runner.py",
            "--corpus",
            str(corpus),
            "--tier",
            "smoke",
            "--report",
            str(rep),
        ],
        timeout=900,
    )


def _run_api_cases(uid: str) -> List[Dict[str, Any]]:
    token = _api_token()
    if not token:
        return [{"id": "api", "ok": False, "error": "API_TOKEN missing"}]
    out: List[Dict[str, Any]] = []
    for case in API_CASES:
        cid = case["id"]
        ep = case.get("endpoint", "chat")
        row: Dict[str, Any] = {"id": cid, "endpoint": ep}
        try:
            if ep == "ping":
                row["result"] = _api_get("/api/v1/ops/ping", token)
            elif ep == "ops_probe":
                row["result"] = _api_post(
                    "/api/v1/ops/probe",
                    token,
                    {"user_id": uid, "message": case["text"], "channel": "full_system_probe"},
                )
            else:
                row["result"] = _api_post(
                    "/api/v1/chat",
                    token,
                    {"user_id": uid, "message": case["text"], "channel": "full_system_probe"},
                )
            r = row.get("result") or {}
            row["ok"] = bool(r.get("ok")) or r.get("status") == 200
            if isinstance(r.get("json"), dict):
                j = r["json"]
                trace = j.get("trace") if isinstance(j.get("trace"), dict) else {}
                row["assistant"] = (trace.get("assistant_text") or j.get("reply") or "")[:500]
                row["issues"] = trace.get("issues") or []
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)[:300]
        out.append(row)
    return out


def _run_telegram_suite(py: str, suite: str, gap: float, timeout: float) -> Dict[str, Any]:
    out_json = _ROOT / "data" / "benchmarks" / f"tg_{suite}_{_now_slug()}.json"
    r = _run(
        [py, "scripts/agent_telegram_client.py", "--suite", suite, "--timeout", str(timeout), "--json-out", str(out_json)],
        timeout=timeout * 8 + 60,
    )
    if out_json.is_file():
        try:
            r["report"] = json.loads(out_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    time.sleep(gap)
    return r


def _run_telegram_modules(py: str, gap: float, timeout: float) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for case in MODULE_CASES:
        out_json = _ROOT / "data" / "benchmarks" / f"tg_{case['id']}_{_now_slug()}.json"
        r = _run(
            [
                py,
                "scripts/agent_telegram_client.py",
                "--text",
                case["text"],
                "--timeout",
                str(timeout),
                "--json-out",
                str(out_json),
            ],
            timeout=timeout + 90,
        )
        texts: List[str] = []
        if out_json.is_file():
            try:
                tg = json.loads(out_json.read_text(encoding="utf-8"))
                texts = [x.get("text", "") for x in tg.get("replies") or []]
            except json.JSONDecodeError:
                pass
        ok = bool(r.get("ok")) and _check_text(texts, case.get("expect_any") or [])
        rows.append(
            {
                "id": case["id"],
                "text": case["text"],
                "ok": ok,
                "exit_code": r.get("exit_code"),
                "replies": texts[:2],
                "elapsed_ms": r.get("elapsed_ms"),
            }
        )
        time.sleep(gap)
    return rows


def _collect_metrics() -> Dict[str, Any]:
    def tail_jsonl(path: Path, n: int = 12) -> List[Dict[str, Any]]:
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return rows

    llm = tail_jsonl(_ROOT / "data" / "llm_usage.jsonl", 15)
    qa = tail_jsonl(_ROOT / "data" / "runtime" / "quality_audit.jsonl", 8)
    return {
        "llm_flash_tail": [
            {
                "ts": r.get("ts"),
                "tag": r.get("tag"),
                "prompt": r.get("prompt_tokens"),
                "cached": r.get("cached_prompt_tokens"),
                "session": r.get("session_id"),
            }
            for r in llm
            if "deepseek" in str(r.get("requested_model") or "")
        ][-8:],
        "quality_tail": [
            {"ts": (r.get("ts") or "")[:19], "issues": r.get("issues"), "user": (r.get("user_excerpt") or "")[:40]}
            for r in qa
        ],
        "llama_calls_in_tail": sum(1 for r in llm if "llama" in str(r.get("requested_model") or "").lower()),
    }


def _write_md(report: Dict[str, Any], path: Path) -> None:
    lines = [
        f"# Full system integration report",
        f"",
        f"**TS:** {report.get('ts')}",
        f"**Host:** {report.get('host', 'local')}",
        f"",
        f"## Summary",
        f"",
    ]
    s = report.get("summary") or {}
    for k, v in s.items():
        lines.append(f"- **{k}:** {v}")
    lines.append("")
    lines.append("## Failures / warnings")
    lines.append("")
    for item in report.get("findings") or []:
        lines.append(f"- [{item.get('severity')}] {item.get('id')}: {item.get('detail')}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=float(os.getenv("PROBE_GAP_SEC", "18")))
    ap.add_argument("--tg-timeout", type=float, default=90.0)
    ap.add_argument("--skip-telegram", action="store_true")
    ap.add_argument("--skip-api", action="store_true")
    ap.add_argument("--skip-runner", action="store_true")
    ap.add_argument(
        "--probe-user",
        default=default_probe_user_id() or "900000001",
        help="Telegram user_id для probe (env POST_DEPLOY_PROBE_USER_ID / OWNER_TELEGRAM_ID)",
    )
    ap.add_argument(
        "--api-user",
        default=os.getenv("POST_DEPLOY_API_PROBE_USER_ID", "full_system_api_probe"),
        help="Отдельный user_id для API-кейсов (не смешивать с Telegram probe user)",
    )
    args = ap.parse_args()

    _load_dotenv()
    py = str(_ROOT / "venv" / "bin" / "python")
    if not Path(py).is_file():
        py = sys.executable

    slug = _now_slug()
    report: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "host": os.getenv("HOSTNAME", "local"),
        "sections": {},
        "findings": [],
        "summary": {},
    }

    # API start
    api_start = _run(["bash", "scripts/gemma_api.sh", "start"], timeout=30)
    report["sections"]["api_start"] = api_start

    if not args.skip_runner:
        report["sections"]["pytest"] = _run_pytest_bundle(py)
        report["sections"]["agent_smoke"] = _run_smoke_runner(py)

    if not args.skip_api:
        report["sections"]["api_cases"] = _run_api_cases(str(args.api_user))

    if not args.skip_telegram:
        report["sections"]["tg_tasks"] = _run_telegram_suite(py, "tasks", args.gap, args.tg_timeout)
        report["sections"]["tg_master_plan_v1"] = _run_telegram_suite(py, "master_plan_v1", args.gap, args.tg_timeout)
        report["sections"]["tg_modules"] = _run_telegram_modules(py, args.gap, args.tg_timeout)

    report["metrics"] = _collect_metrics()

    # Analyze findings
    findings: List[Dict[str, Any]] = []

    if not report["sections"].get("pytest", {}).get("ok", True) and not report["sections"].get("pytest", {}).get("skipped"):
        findings.append({"severity": "FAIL", "id": "pytest", "detail": "route/regression pytest failed"})

    smoke = report["sections"].get("agent_smoke") or {}
    if smoke.get("skipped"):
        findings.append({"severity": "WARN", "id": "agent_smoke", "detail": smoke.get("reason", "skipped")})
    elif not smoke.get("ok", True):
        findings.append({"severity": "FAIL", "id": "agent_smoke", "detail": "smoke runner failed"})

    for row in report["sections"].get("api_cases") or []:
        if not row.get("ok"):
            findings.append({"severity": "FAIL", "id": row.get("id"), "detail": row.get("error") or "api fail"})

    for label, key in (("tasks", "tg_tasks"), ("master_plan_v1", "tg_master_plan_v1")):
        if key not in report["sections"]:
            continue
        sec = report["sections"].get(key) or {}
        rep = sec.get("report") or {}
        summ = rep.get("summary") or {}
        if summ:
            p, t = summ.get("pass", 0), summ.get("total", 0)
            if p < t:
                findings.append({"severity": "FAIL", "id": key, "detail": f"Telegram {label} {p}/{t}"})
        elif not sec.get("ok"):
            findings.append({"severity": "FAIL", "id": key, "detail": f"Telegram suite {label} error"})

    if "tg_modules" in report["sections"]:
        for row in report["sections"].get("tg_modules") or []:
            if not row.get("ok"):
                findings.append(
                    {
                        "severity": "WARN",
                        "id": row.get("id"),
                        "detail": f"module check failed: {(row.get('replies') or ['no reply'])[0][:80]}",
                    }
                )

    if report["metrics"].get("llama_calls_in_tail"):
        findings.append({"severity": "FAIL", "id": "mem0_llama", "detail": "llama calls in llm_usage tail"})

    qa_tail = report["metrics"].get("quality_tail") or []
    if any("search_skipped" in (r.get("issues") or []) for r in qa_tail):
        findings.append({"severity": "WARN", "id": "search_skipped", "detail": "quality_audit still has search_skipped"})

    report["findings"] = findings
    fails = sum(1 for f in findings if f.get("severity") == "FAIL")
    warns = sum(1 for f in findings if f.get("severity") == "WARN")
    report["summary"] = {
        "failures": fails,
        "warnings": warns,
        "status": "PASS" if fails == 0 else "FAIL",
    }

    out_dir = _ROOT / "data" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"full_system_report_{slug}.json"
    md_path = out_dir / f"full_system_report_{slug}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(report, md_path)
    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    print(f"STATUS={report['summary']['status']} fails={fails} warns={warns}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
