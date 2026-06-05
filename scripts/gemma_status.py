#!/usr/bin/env python3
"""
Сводка «всё ли в порядке» для владельца и агента Cursor (read-only).

    python scripts/gemma_status.py
    python scripts/gemma_status.py --json
    python scripts/gemma_status.py --smoke          # + release_guard smoke (~1–2 мин)
    python scripts/gemma_status.py --online         # + Telegram/OpenRouter ping

Не деплоит, не меняет .env. На сервере: cd /opt/gemma_agent && python3 scripts/gemma_status.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.owner_diag import (  # noqa: E402
    collect_owner_diag,
    format_owner_diag_markdown,
)


def _run_subprocess(cmd: List[str], timeout: int = 180) -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        tail = ((r.stdout or "") + (r.stderr or "")).strip()[-500:]
        return r.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout}s"
    except OSError as e:
        return False, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="Сводка состояния gemma_bot (read-only)")
    ap.add_argument("--json", action="store_true", help="JSON вместо текста")
    ap.add_argument("--smoke", action="store_true", help="Добавить release_guard --smoke")
    ap.add_argument("--online", action="store_true", help="Проверить Telegram + OpenRouter")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    st = collect_owner_diag()
    checks = st.setdefault("checks", [])

    ok_rules, tail_rules = _run_subprocess(
        [sys.executable, str(ROOT / "scripts" / "check_cursor_rules_health.py")],
        timeout=30,
    )
    checks.append({"id": "cursor_rules_health", "ok": ok_rules, "tail": tail_rules})
    if not ok_rules:
        st.setdefault("problems", []).append("проверка .cursor/rules не прошла")
        st["ok"] = False
        st["problem_count"] = len(st.get("problems") or [])

    if args.smoke:
        ok_smoke, tail_smoke = _run_subprocess(
            [sys.executable, str(ROOT / "scripts" / "release_guard.py"), "--smoke"],
            timeout=300,
        )
        checks.append({"id": "release_guard_smoke", "ok": ok_smoke, "tail": tail_smoke})
        if not ok_smoke:
            st.setdefault("problems", []).append("release_guard --smoke не прошёл")
            st["ok"] = False
            st["problem_count"] = len(st.get("problems") or [])

    if args.online:
        ok_net, tail_net = _run_subprocess(
            [sys.executable, str(ROOT / "scripts" / "check_connectivity.py")],
            timeout=45,
        )
        checks.append({"id": "connectivity", "ok": ok_net, "tail": tail_net})
        if not ok_net:
            st.setdefault("problems", []).append("нет связи Telegram/OpenRouter")
            st["ok"] = False
            st["problem_count"] = len(st.get("problems") or [])

    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        text = format_owner_diag_markdown(st)
        for c in checks:
            if c.get("id") in ("cursor_rules_health", "release_guard_smoke", "connectivity"):
                mark = "OK" if c.get("ok") else "FAIL"
                text += f"\n  [{mark}] {c.get('id')}"
        text += "\nПоиск: python scripts/turns_search.py \"слово\" --days 7"
        print(text)

    return 0 if st.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
