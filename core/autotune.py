"""
«Самообучающийся страж» — сбор метрик производительности и адаптация профилей.

Записывает performance_log.jsonl после каждого ответа.
Раз в час анализирует логи и error_memory, корректирует autotune_state.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Self-verify cooldown ──
# Порог: N bad_fix за последние 10 минут отключает self-verify на 5 минут.
_BAD_FIX_THRESHOLD = int(os.getenv("AUTOTUNE_BAD_FIX_THRESHOLD", "3"))
_BAD_FIX_WINDOW_SEC = int(os.getenv("AUTOTUNE_BAD_FIX_WINDOW_SEC", "600"))  # 10 мин
_SELF_VERIFY_COOLDOWN_SEC = int(os.getenv("AUTOTUNE_SELF_VERIFY_COOLDOWN_SEC", "300"))  # 5 мин


def record_bad_fix(*, profile: str, model: str) -> None:
    """Записать событие плохого fix от self-verify.

    При превышении порога за временное окно self-verify
    автоматически отключается для этого профиля на время cooldown.
    """
    now = datetime.now(timezone.utc).timestamp()
    state = _load_state()
    bad_fix_log = state.setdefault("bad_fix_log", [])
    bad_fix_log.append({"ts": now, "profile": profile, "model": model})
    # Удаляем записи старше окна
    cutoff = now - _BAD_FIX_WINDOW_SEC
    state["bad_fix_log"] = [b for b in bad_fix_log if b.get("ts", 0) > cutoff]

    # Считаем bad_fix за окно для этого профиля
    recent = [b for b in state["bad_fix_log"] if b.get("profile") == profile]
    if len(recent) >= _BAD_FIX_THRESHOLD:
        # Отключаем self-verify для профиля
        cooldowns = state.setdefault("self_verify_cooldowns", {})
        cooldowns[profile] = now + _SELF_VERIFY_COOLDOWN_SEC
        logger.info(
            "[autotune] self-verify suppressed for profile=%s "
            "(%d bad_fix in %.0fs, cooldown=%.0fs)",
            profile, len(recent), _BAD_FIX_WINDOW_SEC, _SELF_VERIFY_COOLDOWN_SEC,
        )
        state["last_suppression_ts"] = now
        state["last_suppression_profile"] = profile
        state["last_suppression_reason"] = f"{len(recent)} bad_fix in {_BAD_FIX_WINDOW_SEC}s"

    _save_state(state)
    # Также пишем в performance_log
    record_performance({"event": "bad_fix", "profile": profile, "model": model})


def is_self_verify_suppressed(profile: str) -> bool:
    """Проверить, отключён ли self-verify для профиля (активный cooldown)."""
    state = _load_state()
    cooldowns = state.get("self_verify_cooldowns") or {}
    until = cooldowns.get(profile, 0.0)
    if until <= datetime.now(timezone.utc).timestamp():
        return False
    return True


# ── Performance log ──


_PERF_LOG_PATH: Optional[str] = None
_PERF_LOCK = threading.Lock()


def _perf_path() -> str:
    global _PERF_LOG_PATH
    if _PERF_LOG_PATH:
        return _PERF_LOG_PATH
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    p = os.path.join(base, "performance_log.jsonl")
    _PERF_LOG_PATH = p
    return p


def _autotune_state_path() -> str:
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    return os.path.join(base, "autotune_state.json")


def record_performance(entry: Dict[str, Any]) -> None:
    """Записать одну строку в performance_log.jsonl после ответа."""
    path = _perf_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **entry,
        }
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        with _PERF_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        logger.warning("[autotune] write failed: %s", e)


# ── State ──


def _load_state() -> Dict[str, Any]:
    path = _autotune_state_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("[autotune] load state error: %s", e)
    return {"rules": {}, "last_analysis_ts": ""}


def _save_state(state: Dict[str, Any]) -> None:
    path = _autotune_state_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("[autotune] save state error: %s", e)


# ── Анализ и адаптация ──


def _read_perf_log(hours: float = 1.0) -> List[Dict[str, Any]]:
    """Прочитать строки из performance_log за последние N часов."""
    path = _perf_path()
    if not os.path.isfile(path):
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    ts_str = row.get("ts", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                        if ts >= cutoff:
                            rows.append(row)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return rows


def _read_error_memory() -> List[Dict[str, Any]]:
    """Прочитать error_analysis.jsonl за последние несколько часов."""
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    path = os.path.join(base, "error_analysis.jsonl")
    if not os.path.isfile(path):
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - 3600 * 4
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    ts_str = row.get("ts", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                        if ts >= cutoff:
                            rows.append(row)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return rows


def _analyze_and_adapt() -> None:
    """Анализ performance_log и error_memory, адаптация autotune_state.json."""
    perf_rows = _read_perf_log(hours=1.0)
    error_rows = _read_error_memory()

    # Группируем по профилю
    by_profile: Dict[str, List[Dict]] = defaultdict(list)
    for r in perf_rows:
        p = str(r.get("profile") or "standard")
        by_profile[p].append(r)

    # Считаем метрики на профиль
    profile_metrics: Dict[str, Dict] = {}
    for prof, rows in by_profile.items():
        total = len(rows)
        ok = sum(1 for r in rows if r.get("self_verify_fix_applied") is False)
        fixed = total - ok
        avg_latency = sum(float(r.get("latency_ms", 0) or 0) for r in rows) / max(total, 1)
        profile_metrics[prof] = {
            "total": total,
            "ok": ok,
            "fixed": fixed,
            "fix_rate": fixed / max(total, 1),
            "avg_latency_ms": avg_latency,
        }

    # Собираем ошибки по типу
    error_counts: Counter = Counter()
    for r in error_rows:
        comp = str(r.get("component") or "")
        msg = str(r.get("message") or "")
        error_counts[f"{comp}:{msg}"] += 1

    # Обновляем state
    state = _load_state()
    state["last_analysis_ts"] = datetime.now(timezone.utc).isoformat()
    state["profile_metrics"] = profile_metrics
    state["error_counts"] = dict(error_counts.most_common(20))

    # Очищаем истёкшие cooldowns
    now = datetime.now(timezone.utc).timestamp()
    cooldowns = state.get("self_verify_cooldowns") or {}
    expired = [p for p, until in cooldowns.items() if until <= now]
    for p in expired:
        del cooldowns[p]
        logger.info("[autotune] self-verify cooldown expired for profile=%s", p)
    state["self_verify_cooldowns"] = cooldowns

    logger.info(
        "[autotune] analysis: profiles=%s errors=%d perf_rows=%d cooldowns=%s",
        {p: m["total"] for p, m in profile_metrics.items()},
        len(error_rows),
        len(perf_rows),
        {p: f"{c - now:.0f}s remaining" for p, c in cooldowns.items()},
    )

    _save_state(state)


# ── Периодическая задача ──


async def _autotune_loop(interval_sec: int = 3600) -> None:
    """Раз в час анализировать логи и адаптироваться."""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            _analyze_and_adapt()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[autotune] loop error: %s", e)


_LOOP_TASK: Optional[asyncio.Task] = None


def start_autotune_loop(interval_sec: int = 3600) -> None:
    """Запустить фоновый цикл автотюнинга."""
    global _LOOP_TASK
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return
    _LOOP_TASK = asyncio.create_task(_autotune_loop(interval_sec=interval_sec))
