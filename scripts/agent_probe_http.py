#!/usr/bin/env python3
"""
HTTP-прогон через api.py (если на сервере поднят API: bash scripts/gemma_api.sh start).

  export API_TOKEN=...
  python scripts/agent_probe_http.py --url http://127.0.0.1:8000 --user-id "$PROBE_USER_ID" --text "привет"

Пауза между вызовами: AGENT_PROBE_HTTP_MIN_INTERVAL_SEC (по умолчанию чуть выше API_RATE_LIMIT_HEAVY_MIN_INTERVAL_SEC).
При 429 ждёт Retry-After и повторяет один раз.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
_LAST_CALL_MONO: float = 0.0


def _default_min_interval_sec() -> float:
    raw = (os.getenv("AGENT_PROBE_HTTP_MIN_INTERVAL_SEC") or "").strip()
    if raw:
        return max(0.0, float(raw))
    server_gap = (os.getenv("API_RATE_LIMIT_HEAVY_MIN_INTERVAL_SEC") or "10").strip()
    try:
        return max(12.0, float(server_gap) + 2.0)
    except ValueError:
        return 12.0


def _throttle_before_request(min_interval: float) -> None:
    global _LAST_CALL_MONO
    if min_interval <= 0:
        return
    now = time.monotonic()
    wait = min_interval - (now - _LAST_CALL_MONO)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL_MONO = time.monotonic()


def _post_json(
    *,
    url: str,
    token: str,
    path: str,
    body: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}{path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _request_with_retry(
    *,
    url: str,
    token: str,
    path: str,
    body: Dict[str, Any],
    timeout: float,
    min_interval: float,
) -> Dict[str, Any]:
    _throttle_before_request(min_interval)
    try:
        return _post_json(url=url, token=token, path=path, body=body, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        retry_after = 0.0
        try:
            retry_after = float(e.headers.get("Retry-After", "0") or 0)
        except ValueError:
            retry_after = 0.0
        if retry_after <= 0:
            retry_after = min_interval or 10.0
        print(
            f"429 rate limit, жду {retry_after:.0f}s и повторяю…",
            file=sys.stderr,
        )
        time.sleep(retry_after)
        _throttle_before_request(min_interval)
        return _post_json(url=url, token=token, path=path, body=body, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("AGENT_PROBE_API_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--token", default=os.getenv("API_TOKEN", ""))
    ap.add_argument(
        "--endpoint",
        choices=("chat", "ops_probe"),
        default=os.getenv("AGENT_PROBE_HTTP_ENDPOINT", "chat"),
        help="chat=/api/v1/chat, ops_probe=/api/v1/ops/probe (полная trace)",
    )
    ap.add_argument(
        "--min-interval",
        type=float,
        default=None,
        help="Секунды между запросами (override AGENT_PROBE_HTTP_MIN_INTERVAL_SEC)",
    )
    ap.add_argument("--timeout", type=float, default=float(os.getenv("AGENT_PROBE_HTTP_TIMEOUT_SEC", "300")))
    args = ap.parse_args()

    token = (args.token or "").strip()
    if not token:
        print("Задайте API_TOKEN в env или --token", file=sys.stderr)
        return 2

    min_interval = args.min_interval if args.min_interval is not None else _default_min_interval_sec()

    if args.endpoint == "ops_probe":
        path = "/api/v1/ops/probe"
        body = {
            "user_id": str(args.user_id),
            "message": args.text,
            "channel": "agent_probe_http",
            "group_id": None,
        }
    else:
        path = "/api/v1/chat"
        body = {
            "user_id": str(args.user_id),
            "message": args.text,
            "channel": "agent_probe_http",
            "group_id": None,
        }

    try:
        data = _request_with_retry(
            url=args.url,
            token=token,
            path=path,
            body=body,
            timeout=args.timeout,
            min_interval=min_interval,
        )
    except urllib.error.HTTPError as e:
        print(e.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
