#!/usr/bin/env python3
"""Единый аудит прод: env, smoke, метрики, логи. Запуск на сервере: python3 scripts/prod_health_audit.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _env_bool(key: str) -> str:
    v = (os.getenv(key) or "").strip().lower()
    return "on" if v in ("1", "true", "yes", "on") else "off"


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    root = Path(os.getenv("GEMMA_PROJECT_ROOT") or _ROOT)
    data = root / "data"
    out: dict = {"ts_utc": datetime.now(timezone.utc).isoformat(), "root": str(root), "checks": []}
    fails = 0

    must_off = (
        "TURN_QUALITY_LOOP_ENABLED",
        "TURN_QUALITY_AUTO_PENDING_CORRECTION",
        "MCE_ENABLED",
        "MCE_AUTO_APPLY",
        "GOAL_RUNNER_AUTO_START",
        "ROUTER_PASSIVE_ENABLED",
    )
    for k in must_off:
        ok = _env_bool(k) == "off"
        out["checks"].append({"id": k, "ok": ok, "value": _env_bool(k)})
        if not ok:
            fails += 1

    hint_on = _env_bool("BRAIN_OPERATOR_CORRECTIONS_IN_HINT") == "on"
    out["checks"].append({"id": "BRAIN_OPERATOR_CORRECTIONS_IN_HINT", "ok": hint_on})
    if not hint_on:
        fails += 1

    searx = (os.getenv("SEARXNG_INSTANCE_URL") or "").strip()
    bad_searx = "search.example.com" in searx
    out["checks"].append({"id": "SEARXNG_URL", "ok": not bad_searx, "value": searx[:80]})
    if bad_searx:
        fails += 1

    for name, rel in (
        ("llm_usage", "llm_usage.jsonl"),
        ("turns", "runtime/turns.jsonl"),
        ("runtime_errors", "runtime_errors.jsonl"),
    ):
        p = data / rel
        out["checks"].append({"id": f"file_{name}", "ok": p.is_file(), "path": str(p)})

    # tail turns issues
    tp = data / "runtime" / "turns.jsonl"
    if tp.is_file():
        from collections import Counter

        issues: Counter = Counter()
        n = 0
        for ln in tp.read_text(encoding="utf-8", errors="replace").splitlines()[-150:]:
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "scenario":
                continue
            n += 1
            for i in d.get("issues") or []:
                issues[i] += 1
            ast = str(d.get("assistant_excerpt") or "")
            if "TOOL_CALL:" in ast:
                issues["leak_TOOL_CALL"] += 1
        out["turns_tail"] = {"n": n, "issues": dict(issues.most_common(12))}
        if issues.get("leak_TOOL_CALL"):
            fails += 1

    runner_py = sys.executable
    if sys.platform != "win32":
        for cand in (root / "venv" / "bin" / "python3",):
            if cand.is_file():
                runner_py = str(cand)
                break
    if (root / "scripts" / "reform_acceptance_runner.py").is_file():
        r = subprocess.run(
            [runner_py, str(root / "scripts" / "reform_acceptance_runner.py")],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = r.returncode == 0
        out["checks"].append(
            {
                "id": "reform_route_regression",
                "ok": ok,
                "note": "route-only 7/7; not Telegram §9",
                "tail": (r.stdout or "")[-400:],
            }
        )
        if not ok:
            fails += 1

    out["summary"] = {"fail_count": fails, "ok": fails == 0}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
