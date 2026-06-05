from __future__ import annotations

import logging

import json
import time
from collections import defaultdict
from typing import Dict


logger = logging.getLogger(__name__)

class MonitoringLayer:
    def __init__(self) -> None:
        self.counters: Dict[str, int] = defaultdict(int)
        self.last_ts = time.time()
        self._history: list = []
        self._MAX_HISTORY = 1000

    def inc(self, key: str, delta: int = 1) -> None:
        self.counters[key] += delta
        self.last_ts = time.time()

    def snapshot(self) -> Dict[str, object]:
        snap = {
            "counters": dict(self.counters),
            "last_ts": self.last_ts,
            "uptime_hint_sec": int(time.time() - self.last_ts),
        }
        self._history.append({"ts": time.time(), "counters": dict(self.counters)})
        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[-self._MAX_HISTORY:]
        return snap

    def persist_snapshot(self, path: str) -> None:
        """Write current snapshot to a time-series JSONL."""
        try:
            snap = {
                "ts": time.time(),
                "counters": dict(self.counters),
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'monitoring', e, exc_info=True)
    def get_history(self, hours: int = 24) -> list:
        """Return snapshots from last N hours."""
        cutoff = time.time() - hours * 3600
        return [h for h in self._history if h["ts"] >= cutoff]

    def compare_week(self, path: str) -> Dict[str, object]:
        """Compare last 24h vs previous 6 days from JSONL."""
        try:
            from collections import Counter
            older = Counter()
            recent = Counter()
            now = time.time()
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        age_hours = (now - row.get("ts", 0)) / 3600
                        if age_hours > 24 * 7:
                            continue
                        cnt = row.get("counters", {})
                        if age_hours <= 24:
                            recent.update(cnt)
                        else:
                            older.update(cnt)
                    except Exception:
                        continue
            keys = set(list(recent.keys())[:10]) | set(list(older.keys())[:10])
            diffs = {}
            for k in keys:
                o = older.get(k, 0) or 1
                r = recent.get(k, 0)
                diffs[k] = {"recent": r, "older_daily": o / 6, "change_pct": round((r - o / 6) / (o / 6) * 100, 1)}
            return diffs
        except Exception:
            return {}


MONITOR = MonitoringLayer()
