from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional

from core.error_analysis import record_error_event


class HeavyTaskWorker:
    """Bounded in-process worker for CPU-heavy/offloaded tasks."""

    def __init__(self) -> None:
        self.enabled = os.getenv("HEAVY_WORKER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_concurrency = max(1, int(os.getenv("HEAVY_WORKER_CONCURRENCY", "2")))
        self.queue_max = max(10, int(os.getenv("HEAVY_WORKER_QUEUE_MAX", "100")))
        # PDF/Office разбор в thread может занимать десятки секунд; 10 с давало TimeoutError на крупных PDF.
        self.timeout_sec = float(os.getenv("HEAVY_WORKER_TIMEOUT_SEC", "60"))
        self._sem = asyncio.Semaphore(self.max_concurrency)
        self._queue_depth = 0

    def can_accept(self) -> bool:
        return self._queue_depth < self.queue_max

    async def submit(self, func: Callable[[], Any], *, tag: str = "heavy_task") -> Optional[Any]:
        if not self.enabled:
            try:
                return await asyncio.to_thread(func)
            except Exception as e:
                record_error_event("task_worker", "direct task failed", exc=e, extra={"tag": tag})
                return None
        if not self.can_accept():
            record_error_event("task_worker", "queue overflow", extra={"code": "WORKER_QUEUE_OVERFLOW", "tag": tag, "queue_depth": self._queue_depth})
            return None
        self._queue_depth += 1
        try:
            async with self._sem:
                try:
                    return await asyncio.wait_for(asyncio.to_thread(func), timeout=self.timeout_sec)
                except Exception as e:
                    record_error_event("task_worker", "task failed", exc=e, extra={"tag": tag, "timeout_sec": self.timeout_sec})
                    return None
        finally:
            self._queue_depth = max(0, self._queue_depth - 1)

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "queue_depth": self._queue_depth,
            "queue_max": self.queue_max,
            "max_concurrency": self.max_concurrency,
            "timeout_sec": self.timeout_sec,
        }


WORKER = HeavyTaskWorker()
