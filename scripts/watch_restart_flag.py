#!/usr/bin/env python3
"""
Внешний watchdog: ждёт появления restart_requested.json (ядро резильенса) и выполняет команду перезапуска.

Запуск на хосте рядом с Docker (не внутри контейнера бота), например:

  export RESTART_WATCH_COMMAND='docker compose -f /path/docker-compose.yml restart bot'
  export RESILIENCE_RUNTIME_DIR=/path/to/project/data/runtime
  python scripts/watch_restart_flag.py

Переменные:
  RESILIENCE_RUNTIME_DIR — каталог с restart_requested.json (default data/runtime от cwd)
  RESTART_WATCH_INTERVAL_SEC — интервал опроса (default 5)
  RESTART_WATCH_COMMAND — обязательно: shell-команда перезапуска контейнера/сервиса
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    cmd = os.getenv("RESTART_WATCH_COMMAND", "").strip()
    if not cmd:
        print("Set RESTART_WATCH_COMMAND (e.g. docker compose restart bot)", file=sys.stderr)
        return 1
    runtime = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
    interval = max(2, int(os.getenv("RESTART_WATCH_INTERVAL_SEC", "5")))
    flag = runtime / "restart_requested.json"
    print(f"Watching {flag} every {interval}s; command: {cmd[:80]}...")
    while True:
        if flag.is_file():
            try:
                data = json.loads(flag.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if data.get("requested"):
                print("Restart flag detected, running command...")
                subprocess.run(cmd, shell=True, check=False)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
