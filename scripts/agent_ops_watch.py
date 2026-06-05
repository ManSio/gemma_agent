#!/usr/bin/env python3
"""
Поток ходов бота в реальном времени (SSE Ops API).

  export API_TOKEN=...
  python scripts/agent_ops_watch.py --url http://HOST_LAN:8000 --user-id "$PROBE_USER_ID"

Требует перезапуск api.py после деплоя core/api_ops.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def _out(msg: str) -> None:
    print(msg, flush=True)


def _fetch_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"X-API-Token": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("AGENT_PROBE_API_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--user-id", default="")
    ap.add_argument("--token", default=os.getenv("API_TOKEN", ""))
    ap.add_argument(
        "--catchup",
        type=int,
        default=8,
        help="Показать последние N ходов до SSE (0 = только новые)",
    )
    args = ap.parse_args()

    token = (args.token or "").strip().strip('"').strip("'")
    if not token:
        _out("Задайте API_TOKEN (в env или --token). Не встраивайте ssh в команду — долгий старт.")
        return 2

    base = args.url.rstrip("/")
    uid = (args.user_id or "").strip()
    if args.catchup > 0:
        q = f"?limit={args.catchup}"
        if uid:
            q += f"&user_id={uid}"
        try:
            snap = _fetch_json(f"{base}/api/v1/ops/turns{q}", token)
            _out(f"--- catchup {snap.get('count', 0)} turns ---")
            for obj in snap.get("turns") or []:
                issues = obj.get("issues") or []
                flag = " ISSUES=" + ",".join(issues) if issues else ""
                _out(
                    f"[{obj.get('ts', '')}] {obj.get('channel', '')} "
                    f"U: {(obj.get('user_text') or '')[:60]!r} "
                    f"A: {(obj.get('assistant_text') or '')[:80]!r}{flag}"
                )
        except Exception as e:
            _out(f"catchup failed: {e}")

    q = f"?user_id={uid}" if uid else ""
    url = f"{base}/api/v1/ops/turns/stream{q}"
    _out(f"SSE {url}")
    req = urllib.request.Request(url, headers={"X-API-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=None) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    _out(payload)
                    continue
                if obj.get("event") in ("connected", "error"):
                    _out(json.dumps(obj, ensure_ascii=False))
                    continue
                issues = obj.get("issues") or []
                flag = " ISSUES=" + ",".join(issues) if issues else ""
                _out(
                    f"[{obj.get('ts', '')}] {obj.get('channel', '')} "
                    f"U: {(obj.get('user_text') or '')[:60]!r} "
                    f"A: {(obj.get('assistant_text') or '')[:80]!r}{flag}"
                )
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        _out(f"stream error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
