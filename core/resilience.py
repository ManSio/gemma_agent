from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from core.error_analysis import record_error_event
from core.number_parse import parse_env_float, parse_env_int


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


class CircuitBreaker:
    """Closed/open/half-open breaker for external dependency storms."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        window_sec: float,
        open_sec: float,
        name: str,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.window_sec = max(1.0, window_sec)
        self.open_sec = max(0.05, open_sec)
        self.name = name
        self._failures: Deque[float] = deque()
        self._open_until: float = 0.0
        self._half_open: bool = False
        self._lock = threading.Lock()

    def _now(self) -> float:
        """Monotonic clock for breaker windows."""
        return time.monotonic()

    def allow_request(self) -> bool:
        """Return False when circuit is open and cooling down."""
        with self._lock:
            now = self._now()
            if self._half_open:
                return True
            if self._open_until > 0.0:
                if now < self._open_until:
                    return False
                self._half_open = True
                self._open_until = 0.0
                return True
            return True

    def record_success(self) -> None:
        """Close circuit after a successful probe or call."""
        with self._lock:
            self._failures.clear()
            self._half_open = False
            self._open_until = 0.0

    def record_failure(self) -> None:
        """Count failure; open circuit when threshold exceeded."""
        with self._lock:
            now = self._now()
            if self._half_open:
                self._half_open = False
                self._open_until = now + self.open_sec
                record_error_event(
                    "resilience",
                    f"circuit {self.name} reopened",
                    extra={"open_sec": self.open_sec},
                )
                return
            cutoff = now - self.window_sec
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()
            self._failures.append(now)
            if len(self._failures) >= self.failure_threshold:
                self._open_until = now + self.open_sec
                self._failures.clear()
                record_error_event(
                    "resilience",
                    f"circuit {self.name} opened",
                    extra={
                        "failure_threshold": self.failure_threshold,
                        "open_sec": self.open_sec,
                    },
                )


_openrouter_breaker: Optional[CircuitBreaker] = None


def openrouter_circuit_breaker() -> CircuitBreaker:
    """Shared OpenRouter circuit breaker (env-tuned)."""
    global _openrouter_breaker
    if _openrouter_breaker is None:
        _openrouter_breaker = CircuitBreaker(
            failure_threshold=max(1, parse_env_int("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5)),
            window_sec=parse_env_float("CIRCUIT_BREAKER_WINDOW_SEC", 60.0),
            open_sec=parse_env_float("CIRCUIT_BREAKER_OPEN_SEC", 300.0),
            name="openrouter",
        )
    return _openrouter_breaker
