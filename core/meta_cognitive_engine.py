"""
Meta-Cognitive Engine (MCE) — единый мета-слой.

Читает 8+ сенсоров (self_model, lessons, experience_memory, route_risk,
healers, observability, autotune, monitor) и принимает решения:
1. Self-State Synthesis — сводка здоровья системы
2. Drift Detection — обнаружение ухудшения (lessons, confidence, latency)
3. Auto-Optimization — предложения по настройке параметров
4. Auto-Apply (MCE_AUTO_APPLY=true) — безопасные рекомендации применяются к .env
5. Experiment Runner — A/B-тестирование, при promote/rollback меняет .env
6. Drift Reaction — при MCE_AUTO_APPLY критические дрейфы меняют env
7. Meta-Learning Loop — учится на своих решениях, подстраивает пороги
8. Meta-Communication (Фаза 8):
   8.1 — Digest: естественно-языковый саммари self-state, дрейфов, экспериментов
   8.2 — /admin_mce_ask: ответы на вопросы о внутреннем состоянии
   8.3 — Self-Goals: цели на основе дрейфов, трекинг прогресса, отчёт

Запускается из MaintenanceBridge с периодичностью MCE_TICK_INTERVAL.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Константы из env ────────────────────────────────────────────────────

_MCE_DRIFT_LESSON_DECAY_DAYS = int(os.getenv("MCE_DRIFT_LESSON_DECAY_DAYS", "7"))
_MCE_CONFIDENCE_RECOVERY_THRESHOLD = float(os.getenv("MCE_CONFIDENCE_RECOVERY_THRESHOLD", "0.4"))
_MCE_HISTORY_MAX = int(os.getenv("MCE_HISTORY_MAX", "100"))

# Автоприменение рекомендаций (false — только наблюдать, true — применять)
_MCE_AUTO_APPLY = os.getenv("MCE_AUTO_APPLY", "false").strip().lower() in {"1", "true", "yes", "on"}
# Порог уверенности, ниже которого автоприменение блокируется
_MCE_AUTO_APPLY_MIN_CONFIDENCE = float(os.getenv("MCE_AUTO_APPLY_MIN_CONFIDENCE", "0.5"))


# ─── SelfState ───────────────────────────────────────────────────────────

@dataclass
class SelfState:
    """Сводка состояния системы в момент синтеза."""
    ts: float = 0.0
    # self_model
    confidence: float = 0.5
    confidence_trend: str = "stable"  # up | stable | down
    safe_mode: bool = False
    # lessons
    lesson_active_count: int = 0
    lesson_avg_effectiveness: float = 0.5
    lesson_total_count: int = 0
    # experience_memory — hit rate
    experience_hit_rate_100: float = 0.0
    # route_risk — количество активных предупреждений
    route_risk_active: int = 0
    # healers
    healer_actions_24h: int = 0
    healer_disabled_modules: List[str] = field(default_factory=list)
    # observability
    p95_telegram_ms: float = 0.0
    p95_openrouter_ms: float = 0.0
    # autotune
    autotune_active_cooldowns: int = 0
    # counters
    failures_last_hour: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Experiment ──────────────────────────────────────────────────────────

@dataclass
class Experiment:
    id: str
    param: str               # "META_INTENT_MIN_CONFIDENCE"
    control_value: str       # "0.3"
    treatment_value: str     # "0.25"
    traffic_fraction: float  # 0.1 (10%)
    started_at: float
    duration_cycles: int     # 50 cycles
    status: str = "running"  # running | promoted | rolled_back
    metric_before: Dict[str, float] = field(default_factory=dict)
    metric_after: Dict[str, float] = field(default_factory=dict)
    rollback_reason: str = ""


# ─── Рекомендация ────────────────────────────────────────────────────────

@dataclass
class MceRecommendation:
    id: str
    ts: float
    reason: str              # что обнаружено
    suggestion: str          # что предлагается
    param: str               # имя env-переменной
    old_value: str
    new_value: str
    status: str = "pending"  # pending | applied | dismissed
    source: str = "mce"      # "mce" — отличить от llm_triage

    def to_triage_style(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "analysis": f"[MCE] {self.suggestion}",
            "priority": "auto",
            "status": self.status,
            "source": self.source,
            "param": self.param,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


# ─── Meta-Learning History Entry ─────────────────────────────────────────

@dataclass
class MceHistoryEntry:
    """Запись о принятом/отвергнутом решении MCE."""
    id: str
    ts: float
    event_type: str         # recommendation_applied | recommendation_dismissed
                             # | experiment_promoted | experiment_rolled_back
                             # | threshold_adjusted
    details: Dict[str, Any] = field(default_factory=dict)


# ─── Goal ────────────────────────────────────────────────────────────────

@dataclass
class MceGoal:
    """Цель, которую MCE ставит себе для самоулучшения."""
    id: str
    description: str         # "снизить p95 openrouter до 10s"
    metric: str              # "p95_openrouter_ms" — ключ в SelfState
    target_value: float      # 10000 (ms)
    baseline_value: float    # 15000
    created_at: float
    deadline_cycles: int     # 720 (~72ч при MCE_TICK_INTERVAL=5)
    last_check: float = 0.0
    status: str = "active"   # active | achieved | abandoned
    achieved_at: Optional[float] = None
    progress_pct: float = 0.0


# ─── Healer flood helpers ─────────────────────────────────────────────────


def _parse_event_ts(raw: Any) -> Optional[float]:
    """Разобрать ISO timestamp из healer.action payload."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _is_mce_noise_healer_event(data: Dict[str, Any]) -> bool:
    """События MCE, которые не должны раздувать healer_flood и triage."""
    if str(data.get("healer") or "") != "MetaCognitiveEngine":
        return False
    action = str(data.get("action") or "")
    return action in {"tighten_healer_thresholds", "drift_detected"}


def count_healer_actions_24h(healer_events: Any) -> int:
    """
    Число healer.action за последние 24 часа (не весь буфер шины).
    Исключает самореференцию MCE (tighten_healer_thresholds / drift_detected).
    """
    cutoff = time.time() - 86400.0
    n = 0
    for e in healer_events or []:
        data = getattr(e, "data", e)
        if not isinstance(data, dict):
            continue
        if _is_mce_noise_healer_event(data):
            continue
        ts = _parse_event_ts(data.get("ts"))
        if ts is None or ts < cutoff:
            continue
        n += 1
    return n


# ─── MCE ─────────────────────────────────────────────────────────────────

class MetaCognitiveEngine:
    """
    Единый мета-когнитивный слой. Запускается из MaintenanceBridge.

    Phase 7.5 — Meta-Learning Loop:
    - Хранит историю принятых/отвергнутых решений
    - Самонастраивает пороги на основе обратной связи
    - Пишет отчёт об обучении каждый N-ный tick
    """

    def __init__(self) -> None:
        self._enabled = os.getenv("MCE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self._tick_interval = max(1, int(os.getenv("MCE_TICK_INTERVAL", "5")))
        self._experiment_enabled = os.getenv("MCE_EXPERIMENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self._experiments_disabled_by_meta = False
        self._tick_counter = 0
        self._last_synthesis: Optional[SelfState] = None
        self._experiment: Optional[Experiment] = None
        self._recommendations: List[MceRecommendation] = []
        self._history: List[MceHistoryEntry] = []
        self._goals: List[MceGoal] = []
        self._last_digest_ts: float = 0.0
        self._lock = RLock()
        # Динамические пороги (могут меняться meta-learning)
        self._dynamic_thresholds: Dict[str, float] = {
            "lesson_eff_low": _MCE_CONFIDENCE_RECOVERY_THRESHOLD,  # 0.4 — но для lessons
            "healer_flood": 30.0,  # Начальный порог 30; meta-learning поднимает выше если реально больше
            "confidence_low": _MCE_CONFIDENCE_RECOVERY_THRESHOLD,
        }
        self._load_experiment()
        self._load_goals()
        self._last_healer_flood_emit_ts: float = 0.0
        self._last_latency_drift_emit_ts: float = 0.0
        self._last_latency_env_ts: float = 0.0

    # ─── Публичный tick (вызывается из MaintenanceBridge) ───────────────

    async def tick(self) -> None:
        """Один мета-когнитивный такт."""
        if not self._enabled:
            return
        self._tick_counter += 1
        if self._tick_counter % self._tick_interval != 0:
            return

        try:
            state = self._synthesize_self_state()
            self._last_synthesis = state

            # Drift detection
            drifts = self._detect_drift(state)
            for d in drifts:
                self._apply_drift(d)

            # Auto-optimization
            opts = self._auto_optimize(state)
            for o in opts:
                self._write_recommendation(o)
            self._auto_apply_recommendations()

            # Experiment
            if self._experiment_enabled:
                await self._tick_experiment(state)

            # Meta-Learning Loop
            self._meta_learning_loop()
            self._maybe_reenable_experiments()

            # Phase 8.3: Self-Goals
            try:
                self._track_goals()
                self._set_goals_from_drifts()
            except Exception as e:
                logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
            # Phase 8.1: Auto-digest (каждый 10-й tick, не чаще)
            try:
                if self._tick_counter % max(1, self._tick_interval * 2) == 0:
                    if self._digest_due():
                        digest = self._build_digest_text()
                        self._last_digest_ts = time.time()
                        await self._send_digest(digest)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
            logger.debug(
                "[MCE] tick #%d | confidence=%.2f trend=%s lessons=%d p95_or=%.0fms drifts=%d opts=%d goals=%d",
                self._tick_counter, state.confidence, state.confidence_trend,
                state.lesson_active_count, state.p95_openrouter_ms,
                len(drifts), len(opts), len(self._goals),
            )
        except Exception as e:
            logger.warning("[MCE] tick error: %s", e, exc_info=True)

    # ─── Self-State Synthesis ───────────────────────────────────────────

    def _synthesize_self_state(self) -> SelfState:
        state = SelfState(ts=time.time())

        # 1. self_model — через default_self_model как fallback
        try:
            from core.self_model import default_self_model
            sm = default_self_model()
            cs = sm.get("confidence_summary") or {}
            state.confidence = float(cs.get("score", 0.5))
            state.confidence_trend = str(cs.get("trend", "stable"))
            ac = sm.get("active_constraints") or {}
            state.safe_mode = bool(ac.get("safe_mode", False))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 2. lessons
        try:
            from core.self_learning.lesson_manager import LessonManager
            lm = LessonManager.get_instance()
            active = lm.load_active_lessons()
            all_lessons = lm.load_all_lessons()
            state.lesson_active_count = len(active)
            state.lesson_total_count = len(all_lessons)
            if active:
                state.lesson_avg_effectiveness = sum(
                    l.effectiveness_score for l in active
                ) / len(active)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 3. experience_memory
        try:
            path = os.getenv("GEMMA_EXPERIENCE_PATH", "")
            if not path:
                root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
                path = os.path.join(root, "data", "runtime", "experience_digest.jsonl")
            p = Path(path)
            if p.exists():
                lines = p.read_text(encoding="utf-8", errors="replace").strip().split("\n")
                window = [l for l in lines if l.strip()][-100:]
                if window:
                    ok_count = 0
                    for line in window:
                        try:
                            rec = json.loads(line)
                            if isinstance(rec, dict) and rec.get("outcome") == "ok":
                                ok_count += 1
                        except (json.JSONDecodeError, TypeError):
                            pass
                    state.experience_hit_rate_100 = ok_count / len(window)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 4. route_risk
        try:
            from core.route_risk_memory import default_path as rr_path, stumble_from_turn_quality_loop
            from core.runtime_telegram_settings import effective_bool as _eff_bool_mce_rr

            rrp = Path(rr_path())
            if rrp.exists():
                count = 0
                _excl_ql = _eff_bool_mce_rr("MCE_EXCLUDE_QUALITY_LOOP_ROUTE_RISK", default=True)
                for line in rrp.read_text(encoding="utf-8", errors="replace").split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict) and rec.get("severity", 0) >= 2:
                            if _excl_ql and stumble_from_turn_quality_loop(
                                str(rec.get("detail") or "")
                            ):
                                continue
                            count += 1
                    except (json.JSONDecodeError, TypeError):
                        pass
                state.route_risk_active = count
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 5. healers
        try:
            from core.event_healers import healers_snapshot
            hs = healers_snapshot()
            mh = hs.get("module_failure_healer") or {}
            state.healer_disabled_modules = mh.get("disabled", [])
            try:
                from core.event_bus import bus
                healer_events = bus.history(n=500, event_type="healer.action")
                state.healer_actions_24h = count_healer_actions_24h(healer_events)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 6. observability
        try:
            from core.observability import OBS
            state.p95_telegram_ms = float(OBS.p95("telegram_pipeline"))
            state.p95_openrouter_ms = float(OBS.p95("openrouter_completion_ms"))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 7. autotune
        try:
            from core.autotune import _load_state
            at_state = _load_state()
            cooldowns = at_state.get("self_verify_cooldowns") or {}
            now = time.time()
            state.autotune_active_cooldowns = sum(
                1 for v in cooldowns.values() if float(v) > now
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # 8. monitor
        try:
            from core.monitoring import MONITOR
            state.failures_last_hour = int(MONITOR.counters.get("module_exec_fail_total", 0))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        return state

    # ─── Drift Detection ────────────────────────────────────────────────

    def _detect_drift(self, state: SelfState) -> List[Dict[str, Any]]:
        """Вернуть список дрейфов (каждый — dict с reason, action, details)."""
        drifts: List[Dict[str, Any]] = []

        # 1. Lesson effectiveness drift (использует динамический порог)
        eff_threshold = self._dynamic_thresholds.get("lesson_eff_low", 0.4)
        if state.lesson_active_count > 3 and state.lesson_avg_effectiveness < eff_threshold:
            drifts.append({
                "reason": "lesson_effectiveness_low",
                "action": "review_lessons",
                "threshold": eff_threshold,
                "details": {
                    "avg_effectiveness": state.lesson_avg_effectiveness,
                    "active_count": state.lesson_active_count,
                },
            })

        # 2. Confidence drift
        if state.confidence_trend == "down" and state.confidence < self._dynamic_thresholds.get("confidence_low", 0.4):
            drifts.append({
                "reason": "confidence_dropping",
                "action": "activate_recovery_bias",
                "details": {
                    "confidence": state.confidence,
                    "trend": state.confidence_trend,
                },
            })

        # 3. Latency drift (порог выше типичного brain+tools, иначе шум каждые 3 мин)
        try:
            latency_p95_ms = float(os.getenv("MCE_LATENCY_P95_DRIFT_MS", "28000"))
        except ValueError:
            latency_p95_ms = 28000.0
        if state.p95_openrouter_ms > latency_p95_ms:
            drifts.append({
                "reason": "openrouter_latency_high",
                "action": "suggest_faster_model",
                "details": {
                    "p95_ms": state.p95_openrouter_ms,
                },
            })

        # 4. Healer flood (использует динамический порог)
        flood_threshold = self._dynamic_thresholds.get("healer_flood", 30.0)
        if state.healer_actions_24h > flood_threshold:
            drifts.append({
                "reason": "healer_action_flood",
                "action": "tighten_healer_thresholds",
                "threshold": flood_threshold,
                "details": {
                    "actions_24h": state.healer_actions_24h,
                },
            })

        return drifts

    def _apply_drift(self, drift: Dict[str, Any]) -> None:
        """Применить дрейф — записать healer.action + env-реакцию если MCE_AUTO_APPLY."""
        action = drift.get("action", "drift_detected")
        reason = drift.get("reason", "")
        if action == "tighten_healer_thresholds":
            old_flood = float(self._dynamic_thresholds.get("healer_flood", 30.0))
            new_flood = min(200.0, old_flood * 1.25 + 5.0)
            self._dynamic_thresholds["healer_flood"] = new_flood
            logger.info(
                "[MCE] healer_flood threshold raised %.0f → %.0f (actions_24h=%s)",
                old_flood,
                new_flood,
                (drift.get("details") or {}).get("actions_24h"),
            )
            try:
                cooldown = float(os.getenv("MCE_HEALER_FLOOD_EMIT_COOLDOWN_SEC", "3600"))
            except ValueError:
                cooldown = 3600.0
            now = time.time()
            if cooldown > 0 and (now - self._last_healer_flood_emit_ts) < cooldown:
                return
            self._last_healer_flood_emit_ts = now
        if reason == "openrouter_latency_high":
            try:
                lat_cooldown = float(os.getenv("MCE_LATENCY_DRIFT_EMIT_COOLDOWN_SEC", "1800"))
            except ValueError:
                lat_cooldown = 1800.0
            now = time.time()
            if lat_cooldown > 0 and (now - self._last_latency_drift_emit_ts) < lat_cooldown:
                return
            self._last_latency_drift_emit_ts = now
        try:
            from core.event_bus import bus
            bus.emit("healer.action", {
                "healer": "MetaCognitiveEngine",
                "action": action,
                "reason": reason,
                "details": drift.get("details", {}),
            })
        except Exception as e:
            logger.debug('%s optional failed: %s', 'meta_cognitive_engine', e, exc_info=True)
        # Env-реакция на критические дрейфы (только при автоприменении)
        if not _MCE_AUTO_APPLY:
            return
        if reason == "confidence_dropping":
            self._set_env("BRAIN_AUTO_REASONING_GATE_CORRECTION", "true")
            logger.info("[MCE] drift reaction: enabled reasoning gate correction (confidence dropping)")
        elif reason == "openrouter_latency_high":
            try:
                env_cooldown = float(os.getenv("MCE_LATENCY_DRIFT_ENV_COOLDOWN_SEC", "1800"))
            except ValueError:
                env_cooldown = 1800.0
            now = time.time()
            last_env = getattr(self, "_last_latency_env_ts", 0.0)
            if env_cooldown > 0 and (now - last_env) < env_cooldown:
                return
            self._last_latency_env_ts = now
            self._set_env("MODEL_SWITCH_THRESHOLD",
                          os.getenv("MODEL_SWITCH_THRESHOLD", "100"))
            logger.info("[MCE] drift reaction: reset model switch threshold (high latency)")

    # ─── Auto-Optimization ──────────────────────────────────────────────

    def _auto_optimize(self, state: SelfState) -> List[MceRecommendation]:
        """Сгенерировать рекомендации на основе состояния."""
        opts: List[MceRecommendation] = []

        # 1. Confidence стабильно высокая → больше автономии
        if state.confidence > 0.8 and state.confidence_trend in ("up", "stable"):
            opts.append(MceRecommendation(
                id=uuid.uuid4().hex[:12],
                ts=time.time(),
                reason=f"confidence={state.confidence:.2f} {state.confidence_trend}",
                suggestion="Снизить META_INTENT_MIN_CONFIDENCE для большей автономии",
                param="META_INTENT_MIN_CONFIDENCE",
                old_value=os.getenv("META_INTENT_MIN_CONFIDENCE", "0.3"),
                new_value="0.25",
            ))

        # 2. Много срабатываний healers → усилить авто-восстановление
        flood_threshold = self._dynamic_thresholds.get("healer_flood", 30.0)
        if state.healer_actions_24h > flood_threshold:
            opts.append(MceRecommendation(
                id=uuid.uuid4().hex[:12],
                ts=time.time(),
                reason=f"healer_actions_24h={state.healer_actions_24h} (>{flood_threshold:.0f})",
                suggestion="Включить RESILIENCE_AUTONOMY_ENABLED для агрессивного восстановления",
                param="RESILIENCE_AUTONOMY_ENABLED",
                old_value=os.getenv("RESILIENCE_AUTONOMY_ENABLED", "false"),
                new_value="true",
            ))

        # 3. Низкая эффективность уроков + много ошибок → отключить self-verify
        if state.lesson_avg_effectiveness < 0.3 and state.route_risk_active > 3:
            opts.append(MceRecommendation(
                id=uuid.uuid4().hex[:12],
                ts=time.time(),
                reason=f"lesson_eff={state.lesson_avg_effectiveness:.2f} route_risk={state.route_risk_active}",
                suggestion="Отключить SELF_VERIFY_ACTIVE — уроки неэффективны",
                param="SELF_VERIFY_ACTIVE",
                old_value=os.getenv("SELF_VERIFY_ACTIVE", "true"),
                new_value="false",
            ))

        # 4. Высокая латентность → более быстрая модель
        _current_model = os.getenv("DEFAULT_LLM_MODEL", "").strip()
        _candidate = "openai/gpt-4.1-nano"
        if state.p95_openrouter_ms > 45000:
            if _candidate and _candidate == _current_model:
                logger.debug("[MCE] skip suggest_faster_model — candidate is the same as current (%s)", _current_model)
            else:
                opts.append(MceRecommendation(
                    id=uuid.uuid4().hex[:12],
                    ts=time.time(),
                    reason=f"p95_openrouter={state.p95_openrouter_ms:.0f}ms",
                    suggestion="Сменить DEFAULT_LLM_MODEL на более быструю",
                    param="DEFAULT_LLM_MODEL",
                    old_value=_current_model,
                    new_value=_candidate,
                ))

        # 5. Lesson effectiveness drift — deprecated уроки
        if state.lesson_active_count > 3 and state.lesson_avg_effectiveness < _MCE_DRIFT_LESSON_DECAY_DAYS / 10:
            opts.append(MceRecommendation(
                id=uuid.uuid4().hex[:12],
                ts=time.time(),
                reason=f"avg_lesson_eff={state.lesson_avg_effectiveness:.2f} < {_MCE_DRIFT_LESSON_DECAY_DAYS/10:.1f}",
                suggestion="Запустить apply_forgetting_curve для отзыва устаревших уроков",
                param="INTERNAL_LESSON_CLEANUP",
                old_value="",
                new_value="apply_forgetting_curve",
            ))

        return opts

    def _write_recommendation(self, rec: MceRecommendation) -> None:
        """Записать рекомендацию в JSONL и в память."""
        with self._lock:
            self._recommendations.append(rec)
            if len(self._recommendations) > 100:
                self._recommendations = self._recommendations[-100:]
        try:
            path = self._rec_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logger.debug("[MCE] write recommendation error: %s", e)

    # ─── Применение env ──────────────────────────────────────────────────

    @staticmethod
    def _env_path() -> Path:
        """Путь к .env файлу."""
        return Path(os.getenv("BOT_ENV_FILE", ".env")).resolve()

    def _set_env(self, param: str, value: str) -> bool:
        """
        Установить env-переменную: os.environ + .env файл.
        Возвращает True если значение изменилось.
        """
        old = os.getenv(param, "")
        if old == value:
            return False  # уже стоит
        os.environ[param] = value
        # Записать в .env файл
        try:
            env_path = self._env_path()
            if env_path.exists():
                content = env_path.read_text(encoding="utf-8", errors="replace")
                import re
                pattern = re.compile(rf"^{re.escape(param)}=.*", re.MULTILINE)
                if pattern.search(content):
                    content = pattern.sub(f"{param}={value}", content)
                else:
                    content += f"\n{param}={value}\n"
                env_path.write_text(content, encoding="utf-8")
            logger.info("[MCE] env set %s=%s (was %s)", param, value, old or "(unset)")
            return True
        except OSError as e:
            logger.warning("[MCE] env file write error %s=%s: %s", param, value, e)
            return True  # os.environ всё равно обновлён

    # ─── Автоприменение рекомендаций ─────────────────────────────────────

    def _auto_apply_recommendations(self) -> None:
        """Применить pending-рекомендации, если confidence позволяет."""
        if not _MCE_AUTO_APPLY:
            return
        state = self._last_synthesis
        if not state or state.confidence < _MCE_AUTO_APPLY_MIN_CONFIDENCE:
            return
        with self._lock:
            pending = [r for r in self._recommendations if r.status == "pending"]
        for rec in pending:
            if self._set_env(rec.param, rec.new_value):
                self.record_recommendation_outcome(rec.id, "applied")

    # ─── Применение эксперимента ─────────────────────────────────────────

    def _apply_experiment_outcome(self, exp: Experiment) -> None:
        """Применить результат эксперимента к env."""
        if exp.status == "promoted":
            self._set_env(exp.param, exp.treatment_value)
            logger.info("[MCE] experiment applied: %s=%s", exp.param, exp.treatment_value)
        elif exp.status == "rolled_back" and exp.control_value:
            # Откат к контрольному значению при rollback
            self._set_env(exp.param, exp.control_value)
            logger.info("[MCE] experiment rolled back: %s=%s", exp.param, exp.control_value)

    @staticmethod
    def _rec_path() -> Path:
        p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
        return p.resolve() / "mce_recommendations.jsonl"

    def list_recommendations(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            all_recs = list(self._recommendations)
        all_recs.sort(key=lambda r: r.ts, reverse=True)
        return [r.to_triage_style() for r in all_recs[:limit]]

    # ─── Experiment Runner ──────────────────────────────────────────────

    async def _tick_experiment(self, state: SelfState) -> None:
        """Проверить активный эксперимент или запустить новый."""
        if not self._experiment_enabled:
            return

        exp = self._experiment
        if exp is None:
            recs = self.list_recommendations(limit=5)
            pending = [r for r in recs if r.get("status") == "pending"]
            if pending:
                p = pending[0]
                exp = Experiment(
                    id=uuid.uuid4().hex[:12],
                    param=p["param"],
                    control_value=p["old_value"],
                    treatment_value=p["new_value"],
                    traffic_fraction=float(os.getenv("MCE_TRAFFIC_FRACTION", "0.1")),
                    started_at=time.time(),
                    duration_cycles=50,
                    metric_before={
                        "p95_or": state.p95_openrouter_ms,
                        "confidence": state.confidence,
                        "fail_rate": state.failures_last_hour,
                    },
                )
                logger.info(
                    "[MCE] experiment planned: param=%s %s→%s",
                    exp.param, exp.control_value, exp.treatment_value,
                )
                self._experiment = exp
                self._save_experiment()
        else:
            # Проверить завершение
            if state.ts - exp.started_at > exp.duration_cycles * self._tick_interval * 180:
                exp.metric_after = {
                    "p95_or": state.p95_openrouter_ms,
                    "confidence": state.confidence,
                    "fail_rate": state.failures_last_hour,
                }
                # Решение: если метрики улучшились → promote, иначе rollback
                p95_before = exp.metric_before.get("p95_or", 0)
                p95_after = exp.metric_after.get("p95_or", 0)
                if p95_before > 0 and p95_after < p95_before * 0.9:
                    exp.status = "promoted"
                    exp_msg = "promoted (p95 improved)"
                else:
                    exp.status = "rolled_back"
                    exp.rollback_reason = "p95 did not improve"
                    exp_msg = "rolled_back (p95 not improved)"
                self._save_experiment()
                # Записать в историю
                self._add_history(
                    event_type=f"experiment_{exp.status}",
                    details={
                        "param": exp.param,
                        "before": exp.metric_before,
                        "after": exp.metric_after,
                    },
                )
                self._apply_experiment_outcome(exp)
                logger.info("[MCE] experiment %s completed: %s", exp.id, exp_msg)

    # ─── Experiment Persistence ─────────────────────────────────────────

    def _exp_path(self) -> Path:
        p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
        return p.resolve() / "mce_experiment.json"

    def _save_experiment(self) -> None:
        if self._experiment is None:
            return
        try:
            path = self._exp_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self._experiment), ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug("[MCE] save experiment error: %s", e)

    def _load_experiment(self) -> None:
        path = self._exp_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("status") == "running":
                self._experiment = Experiment(**{k: data[k] for k in Experiment.__dataclass_fields__ if k in data})
                logger.info("[MCE] restored experiment %s", self._experiment.id)
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.debug("[MCE] load experiment error: %s", e)

    # ─── Phase 7.5: Meta-Learning Loop ──────────────────────────────────

    def _meta_learning_loop(self) -> None:
        """
        Анализирует историю решений MCE и подстраивает пороги.

        Срабатывает каждый MCE_TICK_INTERVAL (внутри tick).
        """
        if len(self._history) < 3:
            return

        # Подсчёт статистики
        total = len(self._history)
        applied = sum(1 for h in self._history if h.event_type == "recommendation_applied")
        dismissed = sum(1 for h in self._history if h.event_type == "recommendation_dismissed")
        promoted = sum(1 for h in self._history if h.event_type == "experiment_promoted")
        rolled_back = sum(1 for h in self._history if h.event_type == "experiment_rolled_back")

        adjustments: List[str] = []

        # Если слишком много отклонённых рекомендаций → ослабить пороги
        if total > 5 and dismissed > applied:
            old_val = self._dynamic_thresholds.get("lesson_eff_low", 0.4)
            new_val = max(0.2, old_val - 0.05)
            if abs(new_val - old_val) > 0.01:
                self._dynamic_thresholds["lesson_eff_low"] = new_val
                adjustments.append(f"lesson_eff_low {old_val:.2f}→{new_val:.2f}")

            old_flood = self._dynamic_thresholds.get("healer_flood", 30.0)
            # Адаптивный шаг: если healers много, поднимаем быстрее
            step = max(1.0, old_flood * 0.1)
            new_flood = min(100.0, old_flood + step)
            if abs(new_flood - old_flood) > 0.1:
                self._dynamic_thresholds["healer_flood"] = new_flood
                adjustments.append(f"healer_flood {old_flood:.0f}→{new_flood:.0f}")

            old_conf = self._dynamic_thresholds.get("confidence_low", 0.4)
            new_conf = max(0.2, old_conf - 0.03)
            if abs(new_conf - old_conf) > 0.01:
                self._dynamic_thresholds["confidence_low"] = new_conf
                adjustments.append(f"confidence_low {old_conf:.2f}→{new_conf:.2f}")

        # Если много неудачных экспериментов → отключить эксперименты (порог из env)
        rollback_min = max(2, int(os.getenv("MCE_META_ROLLBACK_DISABLE_MIN", "4")))
        if rolled_back > promoted and rolled_back >= rollback_min:
            self._experiment_enabled = False
            self._experiments_disabled_by_meta = True
            adjustments.append(f"experiments disabled (rollbacks>={rollback_min})")

        if adjustments:
            self._add_history(
                event_type="threshold_adjusted",
                details={
                    "adjustments": adjustments,
                    "stats": {
                        "applied": applied,
                        "dismissed": dismissed,
                        "promoted": promoted,
                        "rolled_back": rolled_back,
                        "total": total,
                    },
                },
            )
            logger.info("[MCE] meta-learning: %s", "; ".join(adjustments))

    def _maybe_reenable_experiments(self) -> None:
        """Снять meta-lock, если последние решения без откатов и env разрешает эксперименты."""
        if not self._experiments_disabled_by_meta:
            return
        env_on = os.getenv("MCE_EXPERIMENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        if not env_on:
            return
        recent = list(self._history)[-12:]
        recent_rollbacks = sum(1 for h in recent if h.event_type == "experiment_rolled_back")
        if recent_rollbacks > 0:
            return
        self._experiment_enabled = True
        self._experiments_disabled_by_meta = False
        self._add_history(
            event_type="experiment_reenabled",
            details={"reason": "no recent rollbacks in window", "window": 12},
        )
        logger.info("[MCE] experiments re-enabled after meta-learning cooldown")

    # ─── History Management ─────────────────────────────────────────────

    def _add_history(self, event_type: str, details: Dict[str, Any]) -> None:
        entry = MceHistoryEntry(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            event_type=event_type,
            details=details,
        )
        with self._lock:
            self._history.append(entry)
            if len(self._history) > _MCE_HISTORY_MAX:
                self._history = self._history[-_MCE_HISTORY_MAX:]

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            entries = list(self._history)
        entries.sort(key=lambda h: h.ts, reverse=True)
        return [asdict(e) for e in entries[:limit]]

    def record_recommendation_outcome(self, rec_id: str, status: str) -> None:
        """Вызывается из админ-команды при apply/dismiss."""
        with self._lock:
            for rec in self._recommendations:
                if rec.id == rec_id:
                    rec.status = status
                    self._add_history(
                        event_type=f"recommendation_{status}",
                        details={"rec_id": rec_id, "param": rec.param, "reason": rec.reason},
                    )
                    break

    # ─── Snapshot ───────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Снимок для /admin_mce_status."""
        state = self._last_synthesis
        exp = self._experiment
        hist = self.get_history(limit=5)
        return {
            "enabled": self._enabled,
            "tick_counter": self._tick_counter,
            "tick_interval": self._tick_interval,
            "experiment_enabled": self._experiment_enabled,
            "self_state": state.to_dict() if state else None,
            "active_experiment": asdict(exp) if exp else None,
            "recommendations_pending": sum(
                1 for r in self._recommendations if r.status == "pending"
            ),
            "dynamic_thresholds": dict(self._dynamic_thresholds),
            "history_recent": hist,
            "history_total": len(self._history),
            "goals": [asdict(g) for g in self._goals],
            "last_digest_ts": getattr(self, "_last_digest_ts", 0.0),
            "auto_apply": _MCE_AUTO_APPLY,
        }

    # ─── Phase 8.1: MCE Digest — саммари на естественном языке ─────────

    def _build_digest_text(self) -> str:
        """Сгенерировать текстовый саммари состояния MCE на русском."""
        state = self._last_synthesis
        if not state:
            return "MCE ещё не выполнил ни одного синтеза."

        lines: List[str] = []
        lines.append("🤖 <b>MCE Digest</b>\n")

        # Общее состояние
        conf_icon = "🟢" if state.confidence >= 0.6 else "🟡" if state.confidence >= 0.4 else "🔴"
        lines.append(
            f"{conf_icon} <b>Уверенность:</b> {state.confidence:.0%} "
            f"(тренд: {state.confidence_trend})"
        )
        if state.safe_mode:
            lines.append("  ⚠️ Режим safe mode активен")

        # Уроки
        lines.append(
            f"📚 <b>Уроки:</b> {state.lesson_active_count} активных, "
            f"средняя эффективность {state.lesson_avg_effectiveness:.0%}"
        )

        # Healer-действия
        if state.healer_actions_24h > 0:
            lines.append(
                f"🔧 <b>Healer-действия за 24ч:</b> {state.healer_actions_24h}"
            )
        if state.healer_disabled_modules:
            lines.append(
                f"  Отключённые модули: {', '.join(state.healer_disabled_modules)}"
            )

        # Латентность
        lines.append(
            f"⏱ <b>p95:</b> Telegram {state.p95_telegram_ms:.0f}ms, "
            f"OpenRouter {state.p95_openrouter_ms:.0f}ms"
        )

        # Experience hit rate
        lines.append(
            f"🎯 <b>Experience hit-rate:</b> {state.experience_hit_rate_100:.0%}"
        )

        # Route risk
        lines.append(
            f"⚠️ <b>Route risk:</b> {state.route_risk_active} активных предупреждений"
        )

        # Autotune
        if state.autotune_active_cooldowns > 0:
            lines.append(
                f"🔥 <b>Autotune cooldowns:</b> {state.autotune_active_cooldowns}"
            )

        # Монитор
        lines.append(
            f"📊 <b>Сбои за час:</b> {state.failures_last_hour}"
        )

        # Дрейфы (последние)
        drifts = self._detect_drift(state)
        if drifts:
            lines.append(f"\n🧐 <b>Текущие дрейфы ({len(drifts)}):</b>")
            for d in drifts:
                lines.append(f"  • {d.get('reason', '?')} → {d.get('action', '?')}")

        # Эксперимент
        exp = self._experiment
        if exp:
            lines.append(
                f"\n🧪 <b>Эксперимент:</b> {exp.param} {exp.control_value}→{exp.treatment_value}"
                f" ({exp.status})"
            )

        # Цели
        active_goals = [g for g in self._goals if g.status == "active"]
        if active_goals:
            lines.append(f"\n🎯 <b>Цели ({len(active_goals)} активных):</b>")
            for g in active_goals:
                lines.append(
                    f"  • {g.description}: {g.progress_pct:.0f}%"
                    f" (baseline {g.baseline_value}, target {g.target_value})"
                )

        # Рекомендации
        pending = sum(1 for r in self._recommendations if r.status == "pending")
        if pending:
            lines.append(f"\n💡 <b>Рекомендаций в ожидании:</b> {pending}")

        return "\n".join(lines)

    def build_digest(self) -> str:
        """Внешний API для получения digest."""
        return self._build_digest_text()

    def _digest_due(self) -> bool:
        """Проверить, пора ли отправить digest."""
        interval_hours = int(os.getenv("MCE_DIGEST_INTERVAL_HOURS", "6"))
        if interval_hours <= 0:
            return False
        elapsed = time.time() - getattr(self, "_last_digest_ts", 0.0)
        return elapsed >= interval_hours * 3600

    # ─── Phase 8.2: /admin_mce_ask ──────────────────────────────────────

    def ask(self, question: str) -> str:
        """
        Ответить на вопрос пользователя о внутреннем состоянии MCE.

        Не использует LLM — только структурированные данные.
        """
        q = question.strip().lower()
        state = self._last_synthesis

        if not state:
            return "MCE ещё не выполнил синтез состояния."

        # Определить тип вопроса
        if any(w in q for w in ["уверен", "confidence", "как дел", "как ты", "здоров"]):
            lines = [
                f"Моя уверенность: {state.confidence:.0%} (тренд: {state.confidence_trend}).",
                f"Safe mode: {'активен' if state.safe_mode else 'не активен'}.",
                f"Сбоев за последний час: {state.failures_last_hour}.",
            ]
            if state.healer_actions_24h > 0:
                lines.append(f"Healer-действий за 24ч: {state.healer_actions_24h}.")
            if state.route_risk_active > 0:
                lines.append(f"Активных ошибок маршрутизации: {state.route_risk_active}.")
            return " ".join(lines)

        if any(w in q for w in ["латент", "p95", "медлен", "быстр", "скорост"]):
            return (
                f"p95 Telegram: {state.p95_telegram_ms:.0f}ms. "
                f"p95 OpenRouter: {state.p95_openrouter_ms:.0f}ms. "
                f"{'Высокая латентность — слежу за трендом.' if state.p95_openrouter_ms > 10000 else 'Латентность в норме.'}"
            )

        if any(w in q for w in ["урок", "lesson", "обучен"]):
            return (
                f"Активных уроков: {state.lesson_active_count} из {state.lesson_total_count}. "
                f"Средняя эффективность: {state.lesson_avg_effectiveness:.0%}. "
                f"{'Низкая эффективность — рекомендую очистку.' if state.lesson_avg_effectiveness < 0.3 else 'Всё в порядке.'}"
            )

        if any(w in q for w in ["эксперимент", "эксп"]):
            exp = self._experiment
            if exp:
                return (
                    f"Эксперимент: {exp.param} {exp.control_value}→{exp.treatment_value}. "
                    f"Статус: {exp.status}. "
                    f"Запущен {time.strftime('%H:%M %d.%m', time.localtime(exp.started_at))}."
                )
            return "Экспериментов сейчас нет."

        if any(w in q for w in ["цел", "goal", "план"]):
            active = [g for g in self._goals if g.status == "active"]
            if active:
                parts = []
                for g in active:
                    parts.append(f"{g.description} ({g.progress_pct:.0f}%)")
                return "Активные цели:\n" + "\n".join(f"  • {p}" for p in parts)
            return "Текущих целей нет."

        if any(w in q for w in ["дрейф", "проблем", "что не так", "авари"]):
            drifts = self._detect_drift(state)
            if drifts:
                parts = [f"Обнаружено {len(drifts)} дрейфа:"]
                for d in drifts:
                    parts.append(f"  • {d['reason']} → действие: {d['action']}")
                return "\n".join(parts)
            return "Дрейфов не обнаружено. Всё стабильно."

        if any(w in q for w in ["рекомендаци", "совет", "что делать", "оптимизаци"]):
            opts = self._auto_optimize(state)
            if opts:
                parts = [f"У меня {len(opts)} рекомендации:"]
                for o in opts:
                    parts.append(f"  • {o.suggestion} ({o.param}: {o.old_value}→{o.new_value})")
                return "\n".join(parts)
            return "Сейчас нет рекомендаций."

        if any(w in q for w in ["истори", "что произошл", "последн", "событи"]):
            hist = self.get_history(limit=5)
            if hist:
                parts = ["Последние события:"]
                for h in hist:
                    ht = time.strftime("%H:%M", time.localtime(h["ts"]))
                    parts.append(f"  • {h['event_type']} в {ht}")
                return "\n".join(parts)
            return "История пуста."

        # Общий ответ
        return (
            f"Моё состояние: уверенность {state.confidence:.0%} ({state.confidence_trend}), "
            f"уроков {state.lesson_active_count}, "
            f"p95 OR {state.p95_openrouter_ms:.0f}ms, "
            f"active goals {sum(1 for g in self._goals if g.status == 'active')}. "
            f"Спроси подробнее: латентность, уроки, цели, эксперименты, дрейфы."
        )

    # ─── Phase 8.3: Self-Goals ──────────────────────────────────────────

    def _set_goals_from_drifts(self) -> None:
        """Создать цели на основе текущих дрейфов."""
        state = self._last_synthesis
        if not state:
            return

        # Цель 1: снизить p95 openrouter
        if state.p95_openrouter_ms > 12000:
            target = max(5000, state.p95_openrouter_ms * 0.6)
            self._add_goal_if_new(
                description=f"Снизить p95 OpenRouter с {state.p95_openrouter_ms:.0f}ms до {target:.0f}ms",
                metric="p95_openrouter_ms",
                target_value=target,
                baseline_value=state.p95_openrouter_ms,
                deadline_cycles=720,
            )

        # Цель 2: поднять lesson effectiveness
        if state.lesson_active_count > 3 and state.lesson_avg_effectiveness < 0.5:
            target = min(0.8, state.lesson_avg_effectiveness + 0.2)
            self._add_goal_if_new(
                description=f"Поднять среднюю эффективность уроков с {state.lesson_avg_effectiveness:.0%} до {target:.0%}",
                metric="lesson_avg_effectiveness",
                target_value=target,
                baseline_value=state.lesson_avg_effectiveness,
                deadline_cycles=720,
            )

        # Цель 3: снизить flask драконов
        if state.healer_actions_24h > 5:
            target = max(2, state.healer_actions_24h * 0.5)
            self._add_goal_if_new(
                description=f"Снизить healer-действия с {state.healer_actions_24h} до {target:.0f} в сутки",
                metric="healer_actions_24h",
                target_value=target,
                baseline_value=float(state.healer_actions_24h),
                deadline_cycles=1440,
            )

        # Цель 4: поднять confidence
        if state.confidence < 0.6:
            target = min(0.8, state.confidence + 0.2)
            self._add_goal_if_new(
                description=f"Поднять уверенность с {state.confidence:.0%} до {target:.0%}",
                metric="confidence",
                target_value=target,
                baseline_value=state.confidence,
                deadline_cycles=720,
            )

    def _add_goal_if_new(self, description: str, metric: str, target_value: float,
                         baseline_value: float, deadline_cycles: int) -> None:
        """Добавить цель, только если такой ещё нет."""
        metric_only = [g for g in self._goals if g.metric == metric]
        if metric_only:
            return  # цель по этому метрику уже есть
        goal = MceGoal(
            id=uuid.uuid4().hex[:12],
            description=description,
            metric=metric,
            target_value=target_value,
            baseline_value=baseline_value,
            created_at=time.time(),
            deadline_cycles=deadline_cycles,
            progress_pct=0.0,
        )
        self._goals.append(goal)
        self._add_history("goal_created", {
            "description": description,
            "metric": metric,
            "baseline": baseline_value,
            "target": target_value,
        })
        self._save_goals()
        logger.info("[MCE] goal created: %s", description)

    def _track_goals(self) -> None:
        """Обновить прогресс по активным целям."""
        state = self._last_synthesis
        if not state:
            return
        now = time.time()
        state_dict = state.to_dict()
        for g in self._goals:
            if g.status != "active":
                continue
            current = state_dict.get(g.metric, 0.0)
            # Прогресс: насколько приблизились к target от baseline
            if abs(g.target_value - g.baseline_value) > 0.001:
                # Если target < baseline (снижение) — прогресс идёт вниз
                if g.target_value < g.baseline_value:
                    improvement = g.baseline_value - current
                    total_needed = g.baseline_value - g.target_value
                    if total_needed > 0:
                        g.progress_pct = max(0, min(100, (improvement / total_needed) * 100))
                else:
                    # Если target > baseline (повышение) — прогресс идёт вверх
                    improvement = current - g.baseline_value
                    total_needed = g.target_value - g.baseline_value
                    if total_needed > 0:
                        g.progress_pct = max(0, min(100, (improvement / total_needed) * 100))

            g.last_check = now

            # Достигнута?
            if g.target_value < g.baseline_value:
                achieved = current <= g.target_value
            else:
                achieved = current >= g.target_value

            if achieved:
                g.status = "achieved"
                g.achieved_at = now
                g.progress_pct = 100.0
                self._add_history("goal_achieved", {
                    "description": g.description,
                    "metric": g.metric,
                })
                self._save_goals()
                logger.info("[MCE] goal achieved: %s", g.description)

            # Просрочена?
            elapsed_cycles = (now - g.created_at) / (self._tick_interval * 180)
            if elapsed_cycles > g.deadline_cycles and g.status == "active":
                g.status = "abandoned"
                self._add_history("goal_abandoned", {
                    "description": g.description,
                    "metric": g.metric,
                    "progress": g.progress_pct,
                })
                self._save_goals()
                logger.info("[MCE] goal abandoned (timeout): %s", g.description)

    def list_goals(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            sorted_goals = sorted(self._goals, key=lambda g: g.created_at, reverse=True)
            return [asdict(g) for g in sorted_goals[:limit]]

    # ─── Goal Persistence ───────────────────────────────────────────────

    def _goals_path(self) -> Path:
        p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
        return p.resolve() / "mce_goals.json"

    def _save_goals(self) -> None:
        if not self._goals:
            return
        try:
            path = self._goals_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([asdict(g) for g in self._goals], ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug("[MCE] save goals error: %s", e)

    def _load_goals(self) -> None:
        path = self._goals_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for g in data:
                    self._goals.append(MceGoal(**{k: g[k] for k in MceGoal.__dataclass_fields__ if k in g}))
                logger.info("[MCE] loaded %d goals", len(self._goals))
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.debug("[MCE] load goals error: %s", e)

    # ─── Phase 8.1: Digest Delivery ─────────────────────────────────────

    async def _send_digest(self, digest: str) -> None:
        """Отправить digest всем админам."""
        ids: set[str] = set()
        for key in ("ADMIN_NOTIFY_USER_IDS", "ADMIN_USER_IDS"):
            raw = (os.getenv(key) or "").strip()
            if raw:
                ids.update(x.strip() for x in raw.split(",") if x.strip())
        if not ids:
            return
        try:
            from aiogram import Bot
            token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            if not token:
                return
            bot = Bot(token=token, parse_mode="HTML")
            for uid in ids:
                try:
                    await bot.send_message(uid, digest, parse_mode="HTML")
                except Exception as e:
                    logger.debug("[MCE] digest send to %s error: %s", uid, e)
            await bot.session.close()
            logger.info("[MCE] digest sent to %d admins", len(ids))
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[MCE] digest send error: %s", e)


# ─── Глобальный инстанс ─────────────────────────────────────────────────

_MCE: Optional[MetaCognitiveEngine] = None

def get_mce() -> MetaCognitiveEngine:
    global _MCE
    if _MCE is None:
        _MCE = MetaCognitiveEngine()
    return _MCE


__all__ = [
    "MetaCognitiveEngine",
    "SelfState",
    "Experiment",
    "MceRecommendation",
    "MceGoal",
    "get_mce",
]
