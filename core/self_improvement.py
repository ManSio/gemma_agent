"""
Модуль самообучения и автономности.

Содержит:
  1. ProfileReinforcement — closed-loop feedback: подкрепление/ослабление профилей
  2. MetricTimeSeries — хранение истории метрик (time-series)
  3. HealthChecker — проактивная проверка внешних компонентов
  4. install_self_improvement() — регистрация в EventBus
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.event_bus import bus
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 1. ProfileReinforcement — closed-loop feedback
# ═══════════════════════════════════════════════════════════════════


class ProfileReinforcement:
    """Подкрепление/ослабление профилей на основе реакции пользователя.

    После каждого хода записывает, какой профиль использовался и был ли фидбек.
    Статистика хранится в agent_kv (SQLite) для cross-session персистентности.
    Используется в router_classifier для корректировки confidence.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._buffer: List[Dict[str, Any]] = []
        self._flush_interval = float(os.getenv("PROFILE_REINFORCEMENT_FLUSH_INTERVAL", "60.0"))
        self._last_flush = 0.0
        self._min_samples = int(os.getenv("PROFILE_REINFORCEMENT_MIN_SAMPLES", "5"))

    # ── Запись хода ──────────────────────────────────────────────

    def record_turn(
        self,
        profile: str,
        user_feedback_negative: bool,
        user_feedback_positive: bool,
        latency_ms: float,
        success: bool,
    ) -> None:
        """Записать результат одного хода."""
        rec = {
            "ts": time.time(),
            "profile": profile,
            "negative": user_feedback_negative,
            "positive": user_feedback_positive,
            "latency_ms": latency_ms,
            "success": success,
        }
        self._buffer.append(rec)
        MONITOR.inc("self_improvement_turns_total")
        if user_feedback_negative:
            MONITOR.inc("self_improvement_feedback_negative_total")
        if user_feedback_positive:
            MONITOR.inc("self_improvement_feedback_positive_total")

    # ── Получение статистики ──────────────────────────────────────

    def get_profile_score(self, profile: str, window_hours: float = 24.0) -> float:
        """Вернуть скор профиля: 0.0 (плохо) … 1.0 (отлично). База 0.7.

        Учитывает:
          - negative_feedback → штраф -0.15
          - positive_feedback → бонус +0.1
          - success=True → бонус +0.05
          - success=False → штраф -0.1
          - latency > 10s → штраф -0.05
        """
        cutoff = time.time() - window_hours * 3600
        samples = [r for r in self._buffer if r["profile"] == profile and r["ts"] >= cutoff]
        if not samples:
            return 0.7  # нейтральное значение по умолчанию

        score = 0.7
        for s in samples:
            if s.get("negative"):
                score -= 0.15
            if s.get("positive"):
                score += 0.10
            if s.get("success"):
                score += 0.05
            else:
                score -= 0.10
            if s.get("latency_ms", 0) > 10000:
                score -= 0.05

        return max(0.1, min(1.0, score))

    def get_best_profile(self, candidates: List[str]) -> Tuple[str, float]:
        """Выбрать лучший профиль из списка кандидатов по накопленной статистике.

        Возвращает (имя_профиля, скор).
        Если данных нет — возвращает первый кандидат с 0.7.
        """
        if not candidates:
            return ("standard", 0.7)
        best = candidates[0]
        best_score = 0.7
        for p in candidates:
            score = self.get_profile_score(p)
            if score > best_score:
                best = p
                best_score = score
        return best, best_score

    # ── Персистентность ───────────────────────────────────────────

    async def _maybe_flush(self) -> None:
        """Сброс буфера в KV store (раз в _flush_interval)."""
        now = time.time()
        if now - self._last_flush < self._flush_interval:
            return
        async with self._lock:
            buf = self._buffer[:]
            self._buffer.clear()
            self._last_flush = now
        if not buf:
            return
        try:
            from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, set_json

            if agent_kv_enabled():
                branch = agent_kv_branch()
                # Группируем по профилям и пишем агрегат
                by_profile: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                for r in buf:
                    by_profile[r["profile"]].append(r)
                for profile, samples in by_profile.items():
                    agg = self._aggregate(samples)
                    existing = self._read_profile_agg(profile, branch)
                    if existing:
                        agg["total_samples"] = existing.get("total_samples", 0) + agg["total_samples"]
                        agg["negative_total"] = existing.get("negative_total", 0) + agg["negative_total"]
                        agg["positive_total"] = existing.get("positive_total", 0) + agg["positive_total"]
                    set_json(
                        "profile_reinforcement",
                        profile,
                        agg,
                        branch=branch,
                        ttl_sec=86400 * 30,  # 30 дней
                        priority=20,
                    )
                    logger.debug(
                        "[self_improvement] flushed profile=%s samples=%d agg=%s",
                        profile, len(samples), agg,
                    )
        except Exception as e:
            logger.debug("[self_improvement] flush error: %s", e)

    @staticmethod
    def _aggregate(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Агрегация выборки в статистику профиля."""
        n = len(samples)
        if n == 0:
            return {"total_samples": 0, "negative_total": 0, "positive_total": 0, "avg_latency_ms": 0}
        neg = sum(1 for s in samples if s.get("negative"))
        pos = sum(1 for s in samples if s.get("positive"))
        avg_lat = sum(s.get("latency_ms", 0) for s in samples) / n
        return {
            "total_samples": n,
            "negative_total": neg,
            "positive_total": pos,
            "negative_ratio": round(neg / n, 3),
            "positive_ratio": round(pos / n, 3),
            "avg_latency_ms": round(avg_lat, 1),
            "updated_at": time.time(),
        }

    @staticmethod
    def _read_profile_agg(profile: str, branch: str) -> Optional[Dict[str, Any]]:
        try:
            from core.agent_kv.store import get_json
            return get_json("profile_reinforcement", profile, branch=branch)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════
# 2. MetricTimeSeries — история метрик
# ═══════════════════════════════════════════════════════════════════

class MetricTimeSeries:
    """Хранение снимков метрик с временными метками.

    Позволяет ответить на вопрос "как было неделю назад" — сравнивать
    p95, fail_ratio, confidence и другие метрики во времени.
    """

    def __init__(self) -> None:
        self._path = os.getenv("METRIC_TIME_SERIES_PATH", "data/runtime/metrics_timeseries.jsonl")
        self._snapshot_interval = float(os.getenv("METRIC_SNAPSHOT_INTERVAL_SEC", "3600"))  # 1 час
        self._last_snapshot = 0.0
        self._max_lines = int(os.getenv("METRIC_TIME_SERIES_MAX_LINES", "10000"))

    def take_snapshot(self) -> Dict[str, Any]:
        """Собрать текущие метрики и сохранить."""
        snapshot = {
            "ts": time.time(),
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "counters": dict(MONITOR.counters),
            "profiles": self._collect_profile_stats(),
        }
        try:
            from core.observability import OBS
            obs = OBS.snapshot() if hasattr(OBS, "snapshot") else {}
            snapshot["obs"] = obs
        except Exception as e:
            logger.debug('%s optional failed: %s', 'self_improvement', e, exc_info=True)
        try:
            from core.brain.router_classifier import router_status
            rs = router_status()
            if isinstance(rs, dict):
                snapshot["router"] = {
                    "samples": rs.get("samples", 0),
                    "latency_p95": rs.get("latency_ms_p95", 0),
                    "confidence_median": rs.get("confidence_median", 0),
                }
        except Exception as e:
            logger.debug('%s optional failed: %s', 'self_improvement', e, exc_info=True)
        self._append(snapshot)
        MONITOR.inc("metric_snapshots_total")
        return snapshot

    def _append(self, rec: Dict[str, Any]) -> None:
        """Атомарный append в JSONL."""
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logger.debug("[metric_ts] append error: %s", e)
        self._maybe_trim()

    def _maybe_trim(self) -> None:
        """Обрезать файл до _max_lines строк."""
        try:
            if not os.path.isfile(self._path):
                return
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= self._max_lines:
                return
            with open(self._path, "w", encoding="utf-8") as f:
                f.writelines(lines[-self._max_lines:])
        except OSError:
            pass

    def get_delta(self, hours_ago: float = 168) -> Dict[str, Any]:
        """Сравнить метрики сейчас vs N часов назад.

        Args:
            hours_ago: сколько часов назад смотреть (168 = 7 дней)

        Returns: {
            "now": {...},
            "then": {...},
            "delta": {ключ: разница},
            "changes": [список значимых изменений],
        }
        """
        cutoff = time.time() - hours_ago * 3600
        snapshots = self._load_snapshots()
        now_snap = snapshots[-1] if snapshots else {}
        then_snap = next((s for s in reversed(snapshots) if s.get("ts", 0) <= cutoff), None)

        if not then_snap:
            return {"now": now_snap, "then": None, "delta": {}, "changes": ["недостаточно исторических данных"]}

        changes: List[str] = []
        delta: Dict[str, float] = {}

        # Сравниваем router latency
        nr = now_snap.get("router", {})
        tr = then_snap.get("router", {})
        if nr and tr:
            d_lat = (nr.get("latency_p95", 0) or 0) - (tr.get("latency_p95", 0) or 0)
            delta["router_latency_p95_delta"] = round(d_lat, 1)
            if abs(d_lat) > 2000:
                direction = "выросла" if d_lat > 0 else "снизилась"
                changes.append(f"p95 латентность роутера {direction} на {abs(d_lat):.0f}ms")

        # Сравниваем confidence
        nc = nr.get("confidence_median", 0) if nr else 0
        tc = tr.get("confidence_median", 0) if tr else 0
        if nc and tc:
            d_conf = nc - tc
            delta["router_confidence_delta"] = round(d_conf, 2)
            if abs(d_conf) > 0.1:
                direction = "выросла" if d_conf > 0 else "снизилась"
                changes.append(f"уверенность роутера {direction} на {abs(d_conf):.2f}")

        # Сравниваем ключевые счётчики
        ncount = now_snap.get("counters", {})
        tcount = then_snap.get("counters", {})
        for key in set(list(ncount.keys()) + list(tcount.keys())):
            nv = ncount.get(key, 0) or 0
            tv = tcount.get(key, 0) or 0
            diff = nv - tv
            if diff != 0 and abs(diff) > 5:
                delta[f"counter_{key}_change"] = diff
                if diff > 0:
                    changes.append(f"счётчик {key} вырос на {diff}")
                else:
                    changes.append(f"счётчик {key} снизился на {abs(diff)}")

        return {
            "now": now_snap,
            "then": then_snap,
            "delta": delta,
            "changes": changes[-10:],  # последние 10 изменений
            "hours_span": hours_ago,
        }

    def _load_snapshots(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            result = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return result
        except OSError:
            return []

    @staticmethod
    def _collect_profile_stats() -> Dict[str, Any]:
        """Собрать статистику по профилям из router_classifier."""
        try:
            from core.brain.router_classifier import router_status
            rs = router_status()
            if isinstance(rs, dict):
                return {
                    "profiles_in_window": rs.get("profiles", {}),
                    "source_distribution": rs.get("sources", {}),
                }
        except Exception as e:
            logger.debug('%s optional failed: %s', 'self_improvement', e, exc_info=True)
        return {}


# ═══════════════════════════════════════════════════════════════════
# 3. HealthChecker — проактивная проверка компонентов
# ═══════════════════════════════════════════════════════════════════

class HealthChecker:
    """Периодическая проверка внешних компонентов.

    В отличие от event-driven healers (которые ждут ошибок),
    HealthChecker сам инициирует проверки и эмитирует события при проблемах.
    """

    def __init__(self) -> None:
        self._interval = float(os.getenv("HEALTH_CHECK_INTERVAL_SEC", "300"))  # 5 мин
        self._last_check = 0.0
        self._results: Dict[str, Dict[str, Any]] = {}
        self._consecutive_failures: Dict[str, int] = defaultdict(int)

    async def maybe_check(self) -> None:
        """Проверить все компоненты, если прошло достаточно времени."""
        now = time.time()
        if now - self._last_check < self._interval:
            return
        self._last_check = now
        logger.debug("[health] starting health checks")
        results = await asyncio.gather(
            self._check_openrouter(),
            self._check_telegram_api(),
            self._check_qdrant(),
            self._check_mem0(),
            self._check_searxng(),
            return_exceptions=True,
        )
        components = ["openrouter", "telegram_api", "qdrant", "mem0", "searxng"]
        changed = False
        for name, result in zip(components, results):
            if isinstance(result, Exception):
                status = {"ok": False, "error": str(result)[:200]}
            else:
                status = result if isinstance(result, dict) else {"ok": False, "error": "unexpected result"}
            old = self._results.get(name)
            self._results[name] = status
            old_ok = old.get("ok") if isinstance(old, dict) else True
            new_ok = status.get("ok", False)
            if old_ok != new_ok:
                changed = True
                if not new_ok:
                    self._consecutive_failures[name] += 1
                    cf = self._consecutive_failures[name]
                    logger.warning("[health] %s DOWN (consecutive=%d): %s", name, cf, status.get("error"))
                    MONITOR.inc(f"health_{name}_down_total")
                    if cf >= 2:
                        bus.emit("anomaly.detected", {
                            "code": f"health_{name}_down",
                            "severity": "warn" if cf < 5 else "high",
                            "details": {"component": name, "failures": cf, "error": status.get("error")},
                        })
                else:
                    self._consecutive_failures[name] = 0
                    logger.info("[health] %s UP after %d failures", name, self._consecutive_failures.get(name, 0))
                    if old and not old.get("ok"):
                        bus.emit("healer.action", {
                            "healer": "HealthChecker",
                            "action": "component_recovered",
                            "reason": f"{name} снова доступен",
                            "details": {"component": name},
                        })
            if new_ok:
                self._consecutive_failures[name] = 0
        MONITOR.inc("health_checks_total")

    @staticmethod
    async def _check_openrouter() -> Dict[str, Any]:
        """Проверить доступность OpenRouter API."""
        try:
            import aiohttp
            api_key = os.getenv("OPENROUTER_API_KEY", "")
            if not api_key:
                return {"ok": False, "error": "no API key configured"}
            headers = {"Authorization": f"Bearer {api_key}"}
            t0 = time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get("https://openrouter.ai/api/v1/auth/key", headers=headers) as resp:
                    elapsed_ms = (time.time() - t0) * 1000
                    if resp.status == 200:
                        return {"ok": True, "latency_ms": elapsed_ms}
                    return {"ok": False, "error": f"HTTP {resp.status}", "latency_ms": elapsed_ms}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def _check_telegram_api() -> Dict[str, Any]:
        """Проверить доступность Telegram API."""
        try:
            token = os.getenv("TELEGRAM_TOKEN", "")
            if not token:
                return {"ok": False, "error": "no token configured"}
            import aiohttp
            t0 = time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"https://api.telegram.org/bot{token}/getMe") as resp:
                    elapsed_ms = (time.time() - t0) * 1000
                    if resp.status == 200:
                        return {"ok": True, "latency_ms": elapsed_ms}
                    return {"ok": False, "error": f"HTTP {resp.status}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def _check_qdrant() -> Dict[str, Any]:
        """Проверить доступность Qdrant."""
        url = os.getenv("QDRANT_URL", "")
        if not url:
            return {"ok": True, "note": "not configured"}  # не настроен — не ошибка
        try:
            import aiohttp
            t0 = time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{url}/collections") as resp:
                    elapsed_ms = (time.time() - t0) * 1000
                    if resp.status < 500:
                        return {"ok": True, "latency_ms": elapsed_ms}
                    return {"ok": False, "error": f"HTTP {resp.status}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def _check_mem0() -> Dict[str, Any]:
        """Проверить доступность Mem0."""
        api_key = os.getenv("MEM0_API_KEY", "")
        if not api_key:
            return {"ok": True, "note": "not configured"}
        try:
            import aiohttp
            headers = {"Authorization": f"Bearer {api_key}"}
            t0 = time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get("https://api.mem0.ai/v1/users", headers=headers) as resp:
                    elapsed_ms = (time.time() - t0) * 1000
                    return {"ok": resp.status < 500, "latency_ms": elapsed_ms}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def _check_searxng() -> Dict[str, Any]:
        """Проверить доступность SearXNG."""
        url = os.getenv("SEARXNG_BASE_URL", "")
        if not url:
            return {"ok": True, "note": "not configured"}
        try:
            import aiohttp
            t0 = time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url.rstrip("/") + "/health") as resp:
                    elapsed_ms = (time.time() - t0) * 1000
                    return {"ok": resp.status < 500, "latency_ms": elapsed_ms}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "last_check_ts": self._last_check,
            "interval_sec": self._interval,
            "results": dict(self._results),
            "consecutive_failures": dict(self._consecutive_failures),
        }


# ═══════════════════════════════════════════════════════════════════
# Singleton-экземпляры
# ═══════════════════════════════════════════════════════════════════

_profile_reinforcement = ProfileReinforcement()
_metric_ts = MetricTimeSeries()
_health_checker = HealthChecker()


def get_profile_reinforcement() -> ProfileReinforcement:
    return _profile_reinforcement


def get_metric_ts() -> MetricTimeSeries:
    return _metric_ts


def get_health_checker() -> HealthChecker:
    return _health_checker


# ═══════════════════════════════════════════════════════════════════
# Регистрация в EventBus
# ═══════════════════════════════════════════════════════════════════


async def _on_tick(_payload: Dict[str, Any]) -> None:
    """Обработчик maintenance.tick — запуск периодических задач."""
    await _health_checker.maybe_check()
    await _profile_reinforcement._maybe_flush()
    _metric_ts.take_snapshot()


async def _on_turn_outcome(payload: Dict[str, Any]) -> None:
    """Обработчик turn.outcome — запись результата хода для обучения."""
    profile = str(payload.get("profile") or "standard").strip()
    negative = bool(payload.get("user_feedback_negative", False))
    positive = bool(payload.get("user_feedback_positive", False))
    latency = float(payload.get("latency_ms", 0))
    success = bool(payload.get("ok", True))
    _profile_reinforcement.record_turn(
        profile=profile,
        user_feedback_negative=negative,
        user_feedback_positive=positive,
        latency_ms=latency,
        success=success,
    )


_INSTALLED = False


def install_self_improvement() -> None:
    """Зарегистрировать все обработчики в EventBus. Идемпотентно."""
    global _INSTALLED
    if _INSTALLED:
        return
    bus.subscribe_async("maintenance.tick", _on_tick)
    bus.subscribe_async("turn.outcome", _on_turn_outcome)
    _INSTALLED = True
    logger.info("[self_improvement] installed: health_checks, metric_ts, profile_reinforcement")
