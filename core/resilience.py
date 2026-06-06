from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from core.error_analysis import record_error_event


class QueueGuard:
    def __init__(self, max_size: int = 2000) -> None:
        self.max_size = max_size
        self._q: Deque[float] = deque(maxlen=max_size + 50)

    def allow(self) -> bool:
        if len(self._q) >= self.max_size:
            record_error_event("resilience", "queue overflow blocked", extra={"max_size": self.max_size, "size": len(self._q)})
            return False
        self._q.append(time.monotonic())
        return True

    def done(self) -> None:
        if self._q:
            self._q.popleft()


async def with_timeout(
    coro: Awaitable[Any],
    timeout_sec: float,
    tag: str,
    *,
    record_errors: bool = True,
) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except Exception as e:
        if record_errors:
            record_error_event(
                "resilience",
                f"{tag} timeout/failure",
                exc=e,
                extra={"timeout_sec": timeout_sec},
            )
        raise


async def with_retry(
    operation: Callable[[], Awaitable[Any]],
    *,
    retries: int = 2,
    base_delay_sec: float = 0.25,
    tag: str = "retry_op",
    timeout_sec: Optional[float] = None,
    record_errors: bool = True,
) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            if timeout_sec and timeout_sec > 0:
                return await with_timeout(
                    operation(),
                    timeout_sec,
                    tag=f"{tag}_attempt_{attempt}",
                    record_errors=record_errors,
                )
            return await operation()
        except Exception as e:
            last_err = e
            if record_errors:
                record_error_event(
                    "resilience",
                    f"{tag} failed",
                    exc=e,
                    extra={"attempt": attempt, "retries": retries},
                )
            if attempt >= retries:
                break
            await asyncio.sleep(base_delay_sec * (2 ** attempt))
    if last_err:
        raise last_err
    raise RuntimeError(f"{tag} failed without exception")


class TaskWatchdog:
    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._tasks: Dict[str, float] = {}

    def register(self, task_id: str) -> None:
        self._tasks[task_id] = time.monotonic()

    def unregister(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    def check(self) -> Dict[str, float]:
        now = time.monotonic()
        stale: Dict[str, float] = {}
        for tid, ts in list(self._tasks.items()):
            age = now - ts
            if age > self.timeout_sec:
                stale[tid] = age
                record_error_event("resilience", "watchdog stale task", extra={"task_id": tid, "age_sec": age})
        return stale


def fallback_result(message: str, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"ok": False, "fallback": True, "message": message}
    if extra:
        payload["extra"] = extra
    return payload


DEFAULT_TIMEOUT_SEC = float(os.getenv("OP_TIMEOUT_SEC", "90"))
DEFAULT_RETRIES = int(os.getenv("OP_RETRIES", "2"))
