#!/usr/bin/env python3
"""Сравнение ключевых env (без секретов) — локально или на сервере."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Критичные для диагностики прод (значения не секреты)
_KEYS = [
    "PROJECT_ROOT",
    "BEHAVIOR_DATA_DIR",
    "DIALOGUE_MESSAGE_ARCHIVE_ENABLED",
    "DIALOGUE_MESSAGE_ARCHIVE_MAX",
    "BRAIN_STANDARD_RECENT_COUNT",
    "BRAIN_SHORT_RECENT_COUNT",
    "BRAIN_INCOMPLETE_CONTINUE_ENABLED",
    "BRAIN_INCOMPLETE_CONTINUE_MIN_USER_CHARS",
    "TELEGRAM_PIPELINE_PRIVATE_PARALLEL",
    "TELEGRAM_PIPELINE_SERIALIZE_BY_CHAT",
    "EXEC_MODULES_TIMEOUT_SEC",
    "EXECUTION_TIMEOUT_SEC",
    "WEATHER_DIRECT_REPLY_ENABLED",
    "REFLECTION_HEAVY_ENABLED",
    "MCE_AUTO_APPLY",
    "MCE_EXPERIMENT_ENABLED",
    "GOAL_RUNNER_AUTO_START",
    "ROUTER_PASSIVE_ENABLED",
    "LLM_TRIAGE_ENABLED",
    "ROUTE_RISK_CLUSTER_AUTO_LESSON",
    "TURN_OBSERVER_ENABLED",
    "GEMMA_LLM_USAGE_PATH",
]


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=str(_ROOT / ".env"))
    ap.add_argument("--label", default="local")
    ap.add_argument("--root", default="", help="PROJECT_ROOT для resolve data_paths")
    args = ap.parse_args()
    env_path = Path(args.env)
    if args.root:
        os.environ.setdefault("PROJECT_ROOT", str(Path(args.root).resolve()))
    elif env_path.is_file():
        os.environ.setdefault("PROJECT_ROOT", str(env_path.resolve().parent))
    from dotenv import load_dotenv

    load_dotenv(env_path, override=False)
    env_file = _load_env(Path(args.env))
    print(f"=== {args.label} ({args.env}) ===")
    missing = []
    for k in _KEYS:
        v = os.getenv(k) or env_file.get(k) or ""
        if not v:
            missing.append(k)
            print(f"  {k}=<unset>")
        else:
            print(f"  {k}={v}")
    if missing:
        print(f"MISSING ({len(missing)}): {', '.join(missing)}")
    try:
        from core.data_paths import behavior_dir, message_archive_dir

        print(f"  [resolved] behavior_dir={behavior_dir()} exists={behavior_dir().is_dir()}")
        print(
            f"  [resolved] message_archive_dir={message_archive_dir()} "
            f"exists={message_archive_dir().is_dir()}"
        )
        if message_archive_dir().is_dir():
            n = len(list(message_archive_dir().glob("*.json")))
            print(f"  [resolved] archive_files={n}")
    except Exception as e:
        print(f"  [resolved] paths error: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
