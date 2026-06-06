"""
Внутренняя шина событий (Event Bus) — нервная система проекта.

v2: синхронный emit() + async emit_async(), typed events через @dataclass,
    correlation_id для трассировки, опциональная персистентность в JSONL.

Event types (схемы):
  - module.executed      — ModuleExecutedEvent
  - module.failed        — ModuleFailedEvent
  - openrouter.done      — OpenRouterDoneEvent
  - bug_report.collected — BugReportCollectedEvent
  - anomaly.detected     — AnomalyDetectedEvent
  - healer.action        — HealerActionEvent
  - maintenance.tick     — MaintenanceTickEvent
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict

from core.async_spawn import spawn_logged
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Typed event schemas ────────────────────────────────────────────────


@dataclass
class ModuleExecutedEvent:
    module_name: str
    duration_ms: float
    ok: bool
    error: Optional[str] = None


@dataclass
class ModuleFailedEvent:
    module_name: str
    error: str
    traceback: Optional[str] = None


@dataclass
class OpenRouterDoneEvent:
    model: str
    latency_ms: float
    ok: bool
    tokens_total: int = 0
    cost: float = 0.0
    cached_tok: int = 0
    error: Optional[str] = None


@dataclass
class BugReportCollectedEvent:
    user_id: str
    description: str
    username: str = ""
    chat_id: str = ""


@dataclass
class AnomalyDetectedEvent:
    code: str
    severity: str  # "warn" | "high"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealerActionEvent:
    healer: str
    action: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MaintenanceTickEvent:
    interval_sec: int = 0
    cycle_id: int = 0

# ─── Generic event container ────────────────────────────────────────────


@dataclass
class Event:
    event_type: str
    data: Dict[str, Any]
    ts: str
    correlation_id: str = ""


# ─── EventBus ────────────────────────────────────────────────────────────

_EMPTY = object()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def _env_max_history() -> int:
    raw = os.getenv("EVENT_BUS_HISTORY_SIZE", "1000")
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 1000


def _env_persist_path() -> Optional[str]:
    raw = (os.getenv("EVENT_BUS_PERSIST_PATH") or "").strip()
    return raw or None


_FF_QUEUE_MAXSIZE = int(os.getenv("EVENT_BUS_FF_MAXSIZE", "500"))


class EventBus:
    """
    Синхронно-асинхронная шина событий.

    - `emit()` — синхронный вызов (sync-подписчики срабатывают немедленно,
      async-подписчики фоновой задачей через asyncio.create_task)
    - `emit_await()` — дождаться всех подписчиков (и sync, и async)
    - `subscribe()` — синхронный подписчик
    - `subscribe_async()` — async-подписчик
    - `unsubscribe()` / `unsubscribe_async()` — отписка
    - `history()` — последние N событий для отладки
    """

    SyncCB = Callable[[Dict[str, Any]], None]
    AsyncCB = Callable[[Dict[str, Any]], Any]  # awaitable

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[str, list[SyncCB]] = {}
        self._async_subs: Dict[str, list[AsyncCB]] = {}
        self._filters: Dict[str, list[Callable[[Dict[str, Any]], bool]]] = {}
        self._event_history: List[Event] = []
        self._max_history = _env_max_history()
        self._persist_path: Optional[str] = _env_persist_path()
        self._persist_lock = threading.Lock()
        self._cycle_counter = 0
        # Fire-and-forget queue
        self._ff_queue: "asyncio.Queue[tuple[str, Dict[str, Any]]]" | None = None
        self._ff_worker: "asyncio.Task[None] | None" = None
        # Мониторинг очереди
        self._ff_enqueued: int = 0      # сколько событий поставлено в очередь
        self._ff_processed: int = 0     # сколько успешно обработано
        self._ff_failed: int = 0        # сколько упало с ошибкой
        self._ff_overflow: int = 0      # сколько упало в fallback (переполнение)

    # ── Sync subscribe ──────────────────────────────────────────────

    def subscribe(self, event_type: str, callback: SyncCB) -> None:
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: SyncCB) -> None:
        with self._lock:
            subs = self._subscribers.get(event_type, [])
            if callback in subs:
                subs.remove(callback)

    # ── Async subscribe ─────────────────────────────────────────────

    def subscribe_async(self, event_type: str, callback: AsyncCB) -> None:
        with self._lock:
            if event_type not in self._async_subs:
                self._async_subs[event_type] = []
            self._async_subs[event_type].append(callback)

    def unsubscribe_async(self, event_type: str, callback: AsyncCB) -> None:
        with self._lock:
            subs = self._async_subs.get(event_type, [])
            if callback in subs:
                subs.remove(callback)

    # ── Filters ─────────────────────────────────────────────────────

    def add_filter(self, event_type: str, fn: Callable[[Dict[str, Any]], bool]) -> None:
        with self._lock:
            if event_type not in self._filters:
                self._filters[event_type] = []
            self._filters[event_type].append(fn)

    # ── Emit (sync) ─────────────────────────────────────────────────

    def emit(self, event_type: str, data: Any = None) -> None:
        """Синхронный emit: sync-подписчики вызываются сразу,
        async-подписчики запускаются как asyncio.create_task (fire-and-forget)."""
        payload = self._prepare_payload(event_type, data)
        if not self._check_filters(event_type, payload):
            return
        # Sync subs — немедленно
        self._dispatch_sync(event_type, payload)
        # Async subs — в фоне
        self._dispatch_async_background(event_type, payload)

    async def emit_await(self, event_type: str, data: Any = None) -> None:
        """Дождаться всех подписчиков (sync + async)."""
        payload = self._prepare_payload(event_type, data)
        if not self._check_filters(event_type, payload):
            return
        self._dispatch_sync(event_type, payload)
        await self._dispatch_async(event_type, payload)

    # ── Fire-and-forget через фоновую очередь ────────────────────────

    def start_ff_worker(self) -> None:
        """Запустить фоновый воркер для fire-and-forget событий.
        Вызывается один раз при старте бота из main()."""
        if self._ff_queue is not None:
            return
        self._ff_queue = asyncio.Queue(maxsize=_FF_QUEUE_MAXSIZE)
        self._ff_worker = asyncio.create_task(self._ff_worker_loop(), name="event-bus-ff-worker")

    async def shutdown_ff_worker(self) -> None:
        """Остановить фоновый воркер. Вызывается при завершении бота."""
        if self._ff_worker is not None and not self._ff_worker.done():
            self._ff_worker.cancel()
            try:
                await self._ff_worker
            except asyncio.CancelledError:
                pass
        self._ff_queue = None
        self._ff_worker = None

    def emit_ff(self, event_type: str, data: Any = None) -> None:
        """Fire-and-forget: событие уходит в фоновую asyncio.Queue.
        Вызывающий НЕ БЛОКИРУЕТСЯ на подписчиках.
        Подходит для 'turn.outcome', 'cdc.policy.updated', 'module.executed' и т.п."""
        if self._ff_queue is None:
            # На случай если start_ff_worker не был вызван — fallback на старый emit
            self.emit(event_type, data)
            return
        payload = self._prepare_payload(event_type, data)
        if not self._check_filters(event_type, payload):
            return
        try:
            self._ff_queue.put_nowait((event_type, payload))
            self._ff_enqueued += 1
        except asyncio.QueueFull:
            self._ff_overflow += 1
            if self._ff_overflow <= 3:
                logger.warning("event_bus: ff queue full (%d items), falling back to sync emit", self._ff_overflow)
            self._dispatch_sync(event_type, payload)
            self._dispatch_async_background(event_type, payload)
        except Exception as exc:
            logger.error("event_bus: ff queue put error: %s", exc)
            self._dispatch_sync(event_type, payload)
            self._dispatch_async_background(event_type, payload)

    async def _ff_worker_loop(self) -> None:
        """Фоновый воркер: разбирает очередь и диспатчит подписчиков."""
        while True:
            try:
                event_type, payload = await self._ff_queue.get()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("event_bus ff worker get error: %s", exc)
                await asyncio.sleep(0.1)
                continue
            try:
                self._dispatch_sync(event_type, payload)
                await self._dispatch_async(event_type, payload)
                self._ff_processed += 1
            except Exception as exc:
                self._ff_failed += 1
                logger.error("event_bus ff dispatch error %s: %s", event_type, exc)
            finally:
                self._ff_queue.task_done()

    # ── Internal ────────────────────────────────────────────────────

    def _prepare_payload(self, event_type: str, data: Any) -> Dict[str, Any]:
        cid = _correlation_id()
        if isinstance(data, dict):
            payload = dict(data)
        elif hasattr(data, "__dataclass_fields__"):
            payload = asdict(data)
        else:
            payload = {"payload": data}
        payload.setdefault("event_type", event_type)
        payload.setdefault("ts", _now_iso())
        payload.setdefault("correlation_id", cid)
        return payload

    def _check_filters(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Проверить фильтры для event_type. True = пропустить, False = отклонить."""
        with self._lock:
            filters = list(self._filters.get(event_type, []))
        for fn in filters:
            try:
                if not fn(payload):
                    return False
            except Exception as e:
                logger.error("Event filter error %s: %s", event_type, e)
                return False
        return True

    def _record_history(self, ev: Event) -> None:
        if self._max_history <= 0:
            return
        with self._lock:
            self._event_history.append(ev)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]

    def _maybe_persist(self, ev: Event) -> None:
        pp = self._persist_path
        if not pp:
            return
        try:
            line = json.dumps(asdict(ev), ensure_ascii=False, default=str)
            with self._persist_lock:
                with open(pp, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        except Exception as exc:
            logger.debug("event_bus persist error: %s", exc)

    def _dispatch_sync(self, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subscribers.get(event_type, []))
            subs_all = list(self._subscribers.get("*", []))
        ev = Event(
            event_type=event_type,
            data=payload,
            ts=payload.get("ts", ""),
            correlation_id=payload.get("correlation_id", ""),
        )
        self._record_history(ev)
        self._maybe_persist(ev)
        logger.debug("Event: %s cid=%s", event_type, ev.correlation_id)
        for cb in [*subs, *subs_all]:
            try:
                cb(payload)
            except Exception as e:
                cb_name = getattr(cb, "__name__", str(cb))[:80]
                logger.error("Event handler error %s (%s): %s", event_type, cb_name, e)

    def _dispatch_async_background(self, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._async_subs.get(event_type, []))
            subs_all = list(self._async_subs.get("*", []))
        for cb in [*subs, *subs_all]:
            try:
                spawn_logged(
                    self._safe_async_cb(cb, event_type, payload),
                    label=f"event_bus:{event_type}",
                )
            except Exception as e:
                logger.error("Event async dispatch error %s: %s", event_type, e)

    async def _dispatch_async(self, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._async_subs.get(event_type, []))
            subs_all = list(self._async_subs.get("*", []))
        for cb in [*subs, *subs_all]:
            try:
                await cb(payload)
            except Exception as e:
                logger.error("Event async handler error %s: %s", event_type, e)

    @staticmethod
    async def _safe_async_cb(cb: AsyncCB, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            await cb(payload)
        except Exception as e:
            logger.error("Event async background error %s: %s", event_type, e)

    # ── Introspection ───────────────────────────────────────────────

    def history(
        self,
        n: int = 50,
        *,
        event_type: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> List[Event]:
        """Последние N событий, опционально фильтруя по типу/correlation_id."""
        with self._lock:
            pool = list(self._event_history)
        if event_type:
            pool = [e for e in pool if e.event_type == event_type]
        if correlation_id:
            pool = [e for e in pool if e.correlation_id == correlation_id]
        return pool[-n:] if n > 0 else pool

    def subscriber_count(self) -> Dict[str, int]:
        """Количество подписчиков по типам событий (sync + async)."""
        with self._lock:
            sync = {k: len(v) for k, v in self._subscribers.items()}
            async_ = {k: len(v) for k, v in self._async_subs.items()}
        merged: Dict[str, int] = {}
        for k, v in sync.items():
            merged[k] = merged.get(k, 0) + v
        for k, v in async_.items():
            merged[k] = merged.get(k, 0) + v
        return merged

    def total_events_seen(self) -> int:
        with self._lock:
            return len(self._event_history)

    def snapshot(self) -> Dict[str, Any]:
        qsize = self._ff_queue.qsize() if self._ff_queue is not None else 0
        return {
            "total_events_seen": self.total_events_seen(),
            "max_history": self._max_history,
            "persist_path": self._persist_path or "",
            "subscribers": self.subscriber_count(),
            "ff_queue_size": qsize,
            "ff_enqueued": self._ff_enqueued,
            "ff_processed": self._ff_processed,
            "ff_failed": self._ff_failed,
            "ff_overflow": self._ff_overflow,
        }


bus = EventBus()

__all__ = [
    "bus",
    "EventBus",
    "Event",
    "ModuleExecutedEvent",
    "ModuleFailedEvent",
    "OpenRouterDoneEvent",
    "BugReportCollectedEvent",
    "AnomalyDetectedEvent",
    "HealerActionEvent",
    "MaintenanceTickEvent",
]
