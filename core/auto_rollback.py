"""
UndoLog — JSONL-хранилище действий авто-лечения.

Каждый healer при авто-действии пишет UndoEntry.
AutoRollbackEngine проверяет pending-записи через verify_window_sec
и решает: подтвердить (метрики улучшились) или откатить.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class UndoEntry:
    id: str
    ts: float
    healer: str
    action: str
    params: Dict[str, Any]  # old_value, new_value, module_name, etc.
    status: str = "pending"  # pending | confirmed | rolled_back
    verify_window_sec: float = 300.0
    verified_at: Optional[float] = None
    rollback_reason: Optional[str] = None


class UndoLog:
    """Хранилище undo-записей (JSONL-файл + кеш в памяти)."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path or os.getenv("UNDO_LOG_PATH", "data/runtime/undo_log.jsonl"))
        self._lock = threading.Lock()
        self._entries: Dict[str, UndoEntry] = {}
        self._load()

    # ─── Загрузка / запись ───────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._lock:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entry = UndoEntry(**{k: data[k] for k in UndoEntry.__dataclass_fields__ if k in data})
                            self._entries[entry.id] = entry
                        except (json.JSONDecodeError, TypeError, KeyError) as e:
                            logger.debug("undo_log: skip malformed line: %s", e)
        except OSError as e:
            logger.warning("undo_log: load error: %s", e)

    def _append(self, entry: UndoEntry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("undo_log: append error: %s", e)

    def _rewrite(self) -> None:
        """Перезаписать весь файл (после изменения статуса)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(self._path, "w", encoding="utf-8") as f:
                    for entry in self._entries.values():
                        f.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("undo_log: rewrite error: %s", e)

    # ─── CRUD ─────────────────────────────────────────────────────────────

    def add(
        self,
        healer: str,
        action: str,
        params: Dict[str, Any],
        verify_window_sec: float = 300.0,
    ) -> str:
        entry = UndoEntry(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            healer=healer,
            action=action,
            params=dict(params),
            verify_window_sec=verify_window_sec,
        )
        self._entries[entry.id] = entry
        self._append(entry)
        logger.info("[undo_log] added: healer=%s action=%s id=%s", healer, action, entry.id)
        return entry.id

    def confirm(self, entry_id: str) -> bool:
        entry = self._entries.get(entry_id)
        if not entry or entry.status != "pending":
            return False
        entry.status = "confirmed"
        entry.verified_at = time.time()
        self._rewrite()
        logger.info("[undo_log] confirmed: id=%s", entry_id)
        return True

    def rollback(self, entry_id: str, reason: str) -> bool:
        entry = self._entries.get(entry_id)
        if not entry or entry.status != "pending":
            return False
        entry.status = "rolled_back"
        entry.verified_at = time.time()
        entry.rollback_reason = reason
        self._rewrite()
        logger.info("[undo_log] rolled_back: id=%s reason=%s", entry_id, reason)
        return True

    def get(self, entry_id: str) -> Optional[UndoEntry]:
        return self._entries.get(entry_id)

    def list_pending(self) -> List[UndoEntry]:
        return [e for e in self._entries.values() if e.status == "pending"]

    def list_all(self, limit: int = 50) -> List[UndoEntry]:
        sorted_entries = sorted(self._entries.values(), key=lambda e: e.ts, reverse=True)
        return sorted_entries[:limit]

    def pending_ready(self) -> List[UndoEntry]:
        """Записи, у которых истёк verify_window_sec и статус всё ещё pending."""
        now = time.time()
        return [e for e in self._entries.values() if e.status == "pending" and now > e.ts + e.verify_window_sec]


# Глобальный инстанс
_undo_log = UndoLog()


def get_undo_log() -> UndoLog:
    return _undo_log


# ─── AutoRollbackEngine ──────────────────────────────────────────────────

class AutoRollbackEngine:
    """
    Проверяет pending undo-записи с истёкшим verify_window_sec.
    Решает: подтвердить (метрики улучшились) или откатить.

    Запускается из maintenance.tick.
    """

    def __init__(self, undo_log: Optional[UndoLog] = None) -> None:
        self._log = undo_log or _undo_log
        self._last_check = 0.0
        self._cooldown_sec = float(os.getenv("AUTO_ROLLBACK_COOLDOWN_SEC", "60"))

    async def check_pending(self) -> None:
        """Вызывается из maintenance.tick. Проверяет истёкшие записи."""
        now = time.time()
        if now - self._last_check < self._cooldown_sec:
            return
        self._last_check = now

        ready = self._log.pending_ready()
        if not ready:
            return

        logger.info("[rollback] checking %d pending undo entries", len(ready))
        for entry in ready:
            await self._evaluate(entry)

    async def _evaluate(self, entry: UndoEntry) -> None:
        """Оценить одну запись и решить: rollback или confirm."""
        try:
            if entry.healer == "AutoLatencyHealer" and entry.action == "set_env":
                await self._eval_latency(entry)
            elif entry.healer == "ModuleFailureHealer" and entry.action == "auto_disable_module":
                await self._eval_module_disable(entry)
            elif entry.healer == "ModuleFailureHealer" and entry.action == "create_ephemeral_patch":
                # Эфемерные патчи не откатываем — они сами стираются при успехе модуля
                self._log.confirm(entry.id)
            else:
                # Неизвестный тип — подтверждаем
                self._log.confirm(entry.id)
        except Exception as e:
            logger.warning("[rollback] evaluate error id=%s: %s", entry.id, e)

    async def _eval_latency(self, entry: UndoEntry) -> None:
        """Проверить, улучшился ли p95 после setenv."""
        from core.observability import OBS

        old_p95 = entry.params.get("old_p95", 0)
        threshold = entry.params.get("threshold_ms", 10000)

        # Текущий p95 за последние 20+ вызовов
        current_p95 = OBS.p95("openrouter_completion_ms")

        # Два критерия: либо ниже оригинального threshold, либо улучшение на 50%+
        # Это предотвращает ложные подтверждения после одного случайного пика
        if current_p95 <= 0:
            self._log.confirm(entry.id)
            return

        if current_p95 < threshold:
            # Вернулись к норме — подтверждаем
            self._log.confirm(entry.id)
            logger.info(
                "[rollback] latency improved: p95 %.0f→%.0f (below threshold %.0f) — confirmed",
                old_p95, current_p95, threshold,
            )
        elif old_p95 > threshold * 1.5 and current_p95 < old_p95 * 0.6:
            # p95 был значительно выше threshold и снизился вдвое — вероятно, пик прошёл
            self._log.confirm(entry.id)
            logger.info(
                "[rollback] latency recovered from spike: p95 %.0f→%.0f — confirmed",
                old_p95, current_p95,
            )
        else:
            # Не улучшилось относительно threshold — откатываем
            await self._do_rollback(entry, f"p95 not improved relative to threshold: "
                                           f"{old_p95:.0f}→{current_p95:.0f}ms "
                                           f"(threshold={threshold:.0f}ms)")

    async def _eval_module_disable(self, entry: UndoEntry) -> None:
        """Проверить, стабилизировалась ли система после disable модуля."""
        from core.monitoring import MONITOR

        module_name = entry.params.get("module", "?")
        old_failures = entry.params.get("failures", 0)

        # Смотрим, упало ли количество ошибок
        current_fail = int(MONITOR.counters.get("module_exec_fail_total", 0))
        old_total_fail = entry.params.get("old_total_fail", 0)

        if current_fail <= old_total_fail:
            # Ошибки не растут — оставляем
            self._log.confirm(entry.id)
            logger.info(
                "[rollback] module disable effective: %s fails %d→%d — confirmed",
                module_name, old_total_fail, current_fail,
            )
        else:
            # Ошибки растут — откатываем (re-enable модуль)
            await self._do_rollback(entry, f"errors still growing after disable {module_name}")

    async def _do_rollback(self, entry: UndoEntry, reason: str) -> None:
        """Выполнить физический откат действия."""
        try:
            if entry.action == "set_env":
                key = entry.params.get("key", "")
                old_value = entry.params.get("old_value", "")
                if key:
                    if old_value:
                        os.environ[key] = str(old_value)
                    else:
                        os.environ.pop(key, None)
                    logger.info("[rollback] set_env %s=%s", key, old_value)

            elif entry.action == "auto_disable_module":
                module_name = entry.params.get("module", "")
                if module_name:
                    from core.plugin_registry import plugin_registry
                    plugin_registry.enable_module(module_name)
                    logger.info("[rollback] re-enabled module %s", module_name)
                    # Сбрасываем счётчик ошибок
                    from core.event_healers import get_module_failure_healer
                    get_module_failure_healer().reset(module_name)

            elif entry.action == "create_ephemeral_patch":
                module_name = entry.params.get("module", "")
                if module_name:
                    from core.ephemeral_lessons import deactivate_lesson, load_document
                    doc = load_document()
                    for le in (doc.get("lessons") or []):
                        if isinstance(le, dict) and le.get("trigger") == module_name and le.get("active", True):
                            deactivate_lesson(le.get("id", ""))
                            logger.info("[rollback] deactivated ephemeral patch for %s id=%s",
                                        module_name, le.get("id"))

            self._log.rollback(entry.id, reason)

            from core.event_bus import bus
            bus.emit("healer.action", {
                "healer": "AutoRollbackEngine",
                "action": "rollback",
                "reason": reason,
                "details": {
                    "undo_id": entry.id,
                    "healer": entry.healer,
                    "action": entry.action,
                    "params": entry.params,
                },
            })

        except Exception as e:
            logger.warning("[rollback] execute failed id=%s: %s", entry.id, e)
            self._log.rollback(entry.id, f"rollback_execute_error: {e}")


_rollback_engine = AutoRollbackEngine()


def get_rollback_engine() -> AutoRollbackEngine:
    return _rollback_engine


__all__ = [
    "UndoLog",
    "UndoEntry",
    "AutoRollbackEngine",
    "get_undo_log",
    "get_rollback_engine",
]
