import asyncio
import copy
import logging
import os
import re
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

from core.async_spawn import spawn_logged
from core.models import Input, Plan, PlanStep, Output
from core.plugin_controller import PluginController
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine, Role
from core.behavior_store import BehaviorStore, DialogueCompactPending
from core.brain.text_helpers import build_micro_emotion_style as _build_micro_emotion_style, stable_blend_style as _stable_blend_style
from core.user_facts import UserFactsManager
from core.context_builder import ContextBuilder
from core.config_manager import get_config
from core.monitoring import MONITOR
from core.unified_planner import UnifiedPlanner
from core.observability import OBS
from core.behavior_engine import BehaviorEngine
from core.knowledge_engine import KnowledgeEngine
from core.predictive_behavior_engine import PredictiveBehaviorEngine
from core.goal_engine import GoalEngine
from core.operator_rules import (
    brain_context_addon_from_file,
    force_general_intent_by_operator_patterns,
    prefer_general_over_math_from_file,
)
from core.ephemeral_lessons import brain_addon_for_text, force_general_when_math_probe
from core.self_maintenance import SelfMaintenanceCycles
from core.self_improvement_advisor import SelfImprovementAdvisor
from core.resilience_controller import ResilienceController
from core.recovery_autonomy import RecoveryAutonomyLayer
from core.host_resources import get_host_resource_snapshot
from core.env_flags import gemma_core_log_full
from core.live_pulse import record_planner_pulse
from core.usage_learning import record_usage
from core.prompt_routing import (
    brain_fast_chitchat_eligible,
    infer_assistant_expects_reply,
    private_dm_chitchat_continuity_override,
)
from core.brain.text_helpers import recent_dialogue_forbids_service_clarifications
from core.group_chat_policy import load_group_chat_policy
from core.event_bus import bus
from core.cost_controller import build_cost_autopilot_patch, cost_autopilot_enabled
from core.efficiency_report import build_efficiency_snapshot
from core.efficiency_guard import build_efficiency_guard_patch, efficiency_guard_enabled
from core.context_compression import compress_dialogue_summary, compress_recent_dialogue
from core.self_model import hydrate_self_model_from_kv, update_self_model_after_turn
from core.fast_path import fast_path
from core.reasoning_layer import run_reasoning, reset_chain as reasoning_reset_chain, start_reasoning_timer, reasoning_exceeded_time, abort_reasoning
from core.planning_layer import ExecutionPlan, build_plan, TOOL_CHAINS
from core.self_check import self_check_answer
from core.context_binding import ContextBinder
from core.self_healing import log_tool_error, should_auto_reset as self_healing_auto_reset
from core.self_healing import record_response_time as self_healing_record_rt
from core.self_healing import record_error_ts as self_healing_record_err
from core.self_healing import record_tool_call_count as self_healing_record_tools
from core.self_healing import analyze_and_optimize as self_healing_analyze
from core.memory_guard import can_persist_sensitive, log_inferred_attempt
from core.telemetry import telemetry_logger, SELF_OPTIMIZATION_VERSION
from core.memory_store import record_event as episodic_record, get_insights as episodic_insights
from core.pre_llm_plan import PRE_LLM_DIRECT_VARIANTS

logger = logging.getLogger(__name__)

_DIALOG_PLAN_MODULES = frozenset({"chat_orchestrator", "chat-orchestrator", "smartchat"})

_FALLBACK_DIRECT_REPLY_VARIANTS = frozenset(
    {
        "nl_reminder",
        "nl_weekly_schedule",
        "nl_cancel_reminder",
        "geo_nearby",
        "telegram_location",
        "weather_direct",
        "referential_math",
        "affirmative_search",
        "news_direct",
        "news_web_search",
        "news_item_direct",
    }
) | PRE_LLM_DIRECT_VARIANTS


def _sync_brain_context_to_plan_step(step: PlanStep, exec_ctx: Dict[str, Any]) -> None:
    """После execute: телеметрия brain в plan.steps[].context (mem0 раньше копировал dict)."""
    args = getattr(step, "args", None)
    if not isinstance(args, dict) or not isinstance(exec_ctx, dict):
        return
    plan_ctx = args.get("context")
    if not isinstance(plan_ctx, dict):
        args["context"] = exec_ctx
        return
    if plan_ctx is exec_ctx:
        return
    for key in (
        "brain_turn_telemetry",
        "dialogue_state",
        "router_route_audit",
        "kv_session_debug",
        "brain_profile",
        "router_profile",
        "active_dialogue_slot_kind",
        "discourse_action",
        "turn_state",
        "turn_state_audit",
    ):
        if key not in exec_ctx:
            continue
        val = exec_ctx[key]
        if key == "dialogue_state" and isinstance(val, dict):
            ds = plan_ctx.get("dialogue_state")
            if isinstance(ds, dict):
                ds.update(val)
            else:
                plan_ctx["dialogue_state"] = dict(val)
        else:
            plan_ctx[key] = val


def _brain_telemetry_from_plan(plan: Plan) -> Dict[str, Any]:
    """C6: prompt_tokens_est / brain_recent_limit из brain_turn_telemetry или dialogue_state."""
    best_pt = 0
    out: Dict[str, Any] = {}

    def _merge_from_ctx(ctx: Dict[str, Any]) -> None:
        nonlocal best_pt
        if not isinstance(ctx, dict):
            return
        packs: List[Dict[str, Any]] = []
        bt = ctx.get("brain_turn_telemetry")
        if isinstance(bt, dict):
            packs.append(bt)
        ds = ctx.get("dialogue_state")
        if isinstance(ds, dict):
            packs.append(ds)
        for pack in packs:
            try:
                pt = int(pack.get("prompt_tokens_est") or 0)
            except (TypeError, ValueError):
                pt = 0
            if pt < best_pt:
                continue
            best_pt = pt
            out["prompt_tokens_est"] = pt
            try:
                lim = int(pack.get("brain_recent_limit") or 0)
            except (TypeError, ValueError):
                lim = 0
            if lim > 0:
                out["brain_recent_limit"] = lim
            bp = str(
                pack.get("brain_profile") or pack.get("last_brain_profile") or ""
            ).strip()
            if bp:
                out["brain_profile"] = bp

    for step in plan.steps or []:
        args = getattr(step, "args", None) or {}
        if not isinstance(args, dict):
            continue
        ctx = args.get("context")
        if isinstance(ctx, dict):
            _merge_from_ctx(ctx)
    return out


def _emit_module_from_plan(plan: Plan, ds: Dict[str, Any]) -> str:
    mod = str(ds.get("planned_module") or "").strip()
    if mod:
        return mod
    for step in reversed(plan.steps or []):
        mn = str(getattr(step, "module_name", "") or "").strip()
        if mn and mn not in ("__fallback__",):
            return mn
    return ""


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _obs_mark(meta: Any, name: str) -> None:
    if not isinstance(meta, dict):
        return
    tid = meta.get("trace_id")
    if tid:
        OBS.mark(str(tid), name)


def _intent_override_from_text(text: str) -> str:
    """
    Extra intent hints for non-math complex routes.
    Keeps legacy behavior by returning "" when no strong signal found.
    Uses centralized detect_text_intent from intent_heuristics for common intents.
    """
    low = (text or "").strip().lower()
    if not low:
        return ""
    # Long analytical prompts with explicit uncertainty/strategy framing should stay in reasoning.
    # This guards against accidental "explain" routing to school_assistant.
    _reasoning_structured = (
        len(low) >= 260
        and (
            "вопрос:" in low
            or ("если да" in low and "если нет" in low)
            or ("можно ли вообще" in low and "стратег" in low)
        )
        and (
            "рациональн" in low
            or "стратег" in low
            or "неопредел" in low
            or "тополог" in low
            or "история траектории" in low
        )
    )
    if _reasoning_structured:
        return "reasoning"
    # reasoning / logic traces
    if (
        "δ" in low
        or "дельта" in low
        or re.search(r"\b(?:reasoning|reason|рассужд|докажи|выведи|логик|logic)\b", low)
    ):
        return "reasoning"
    # test/benchmark style probes (S/F-series, explicit test mode / regression run)
    if (
        "f-series" in low
        or "s-series" in low
        or re.search(
            r"\b(?:test mode|режим теста|run tests?|unit tests?|regression tests?|"
            r"прогони тест|прогон тест|тестовый режим|бенчмарк)\b",
            low,
        )
    ):
        return "test"
    # Centralized intent detection for explain/creative/news/code
    try:
        from core.intent_heuristics import detect_text_intent

        detected = detect_text_intent(text)
        if detected:
            return detected
    except Exception as e:
        logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
    # teacher/tutorial mode prompts
    if re.search(r"\b(?:teacher|учитель|обучи|научи|урок)\b", low):
        return "teacher"
    # «Напомни переписку» без slash — в модуль dialog_memory_recall (см. DIALOG_RECALL_NL_ROUTE_ENABLED).
    try:
        from core.memory_recall_facade import plain_text_requests_dialog_recall

        if plain_text_requests_dialog_recall(text):
            return "dialog_recall"
    except Exception as e:
        logger.debug("plain_text_requests_dialog_recall: %s", e)
    return ""


def _intent_mode_continuation_lock(text: str, persisted: Optional[Dict[str, Any]]) -> str:
    """
    Protect against drift between TEST and REASONING modes on short continuation turns.
    Returns locked intent or "" when lock is not applicable.
    """
    if not isinstance(persisted, dict):
        return ""
    ds = persisted.get("dialogue_state") if isinstance(persisted.get("dialogue_state"), dict) else {}
    prev_intent = str((ds or {}).get("last_intent") or "").strip().lower()
    if prev_intent not in {"test", "reasoning"}:
        return ""
    low = (text or "").strip().lower()
    if not low or len(low) > 120:
        return ""
    # Explicit mode-switch language should disable lock.
    if re.search(r"\b(?:переключ|switch|смени|mode|режим)\b", low):
        return ""
    continuation_tokens = {
        "дальше",
        "далее",
        "продолжай",
        "continue",
        "ок",
        "окей",
        "ага",
        "угу",
        "да",
        "нет",
        "next",
        "следующий",
    }
    if low in continuation_tokens or re.search(r"^\s*(?:\+\+|ok+|go+|далее)\s*$", low):
        return prev_intent
    return ""


class Orchestrator:
    def __init__(self, plugin_registry: PluginRegistry, policy_engine: PolicyEngine, **kwargs):
        self.plugin_registry = plugin_registry
        self.plugin_controller = PluginController(plugin_registry)
        self.policy_engine = policy_engine
        self.mem0_memory: Optional[Any] = kwargs.get("mem0_memory")
        self.user_system: Optional[Any] = kwargs.get("user_system")
        self.psychology_engine: Optional[Any] = kwargs.get("psychology_engine")
        self.digital_twin: Optional[Any] = kwargs.get("digital_twin")
        self.persona_engine: Optional[Any] = kwargs.get("persona_engine")
        self.group_behavior: Optional[Any] = kwargs.get("group_behavior")
        self.self_programming: Optional[Any] = kwargs.get("self_programming")
        self.behavior_store: BehaviorStore = kwargs.get("behavior_store") or BehaviorStore()
        self.user_facts_manager: UserFactsManager = kwargs.get("user_facts_manager") or UserFactsManager(
            behavior_store=self.behavior_store,
            mem0_memory=self.mem0_memory,
            user_system=self.user_system,
            digital_twin=self.digital_twin,
        )
        self.anti_flood_enabled = os.getenv("ANTI_FLOOD_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_msg_per_10s = max(1, int(os.getenv("MAX_MSG_PER_10S", "7")))
        self.max_same_text = max(1, int(os.getenv("MAX_SAME_TEXT", "3")))
        self.max_cmd_per_10s = max(1, int(os.getenv("MAX_CMD_PER_10S", "4")))
        self.group_cooldown_sec = max(0.0, float(os.getenv("GROUP_COOLDOWN_SEC", "2.0")))
        self.hard_flood_multiplier = max(2, int(os.getenv("HARD_FLOOD_MULTIPLIER", "2")))
        self.anti_flood_response = os.getenv("ANTI_FLOOD_RESPONSE", "Слишком много сообщений подряд. Подожди немного.")
        self._flood_user_events: Dict[str, deque] = defaultdict(lambda: deque(maxlen=80))
        self._flood_chat_events: Dict[str, deque] = defaultdict(lambda: deque(maxlen=240))
        self._group_trigger_ts: Dict[str, float] = {}
        self._flood_warn_ts: Dict[str, float] = {}
        self._context_builder = ContextBuilder()
        self._config = get_config()
        self._planner = UnifiedPlanner()
        self._behavior_engine = BehaviorEngine()
        self._knowledge_engine = kwargs.get("knowledge_engine") or KnowledgeEngine()
        self._predictive = PredictiveBehaviorEngine(enabled=self._config.predictive_behavior_enabled)
        self._goal_engine = GoalEngine(enabled=self._config.goal_engine_enabled)
        self._maintenance = SelfMaintenanceCycles(enabled=self._config.self_maintenance_enabled)
        self._advisor = SelfImprovementAdvisor(enabled=self._config.self_improvement_advisor_enabled)
        self._maintenance_interval_sec = max(60.0, float(self._config.self_maintenance_interval_sec))
        self._predictive_conf_threshold = max(0.0, min(1.0, float(self._config.predictive_confidence_threshold)))
        self._resilience = ResilienceController()
        self._recovery_autonomy = RecoveryAutonomyLayer()
        self._host_adaptation_hints: List[str] = []
        self.context_binder = ContextBinder()
        bus.subscribe("brain.tool_finished", self._on_brain_tool_finished)

    def _on_brain_tool_finished(self, data: Dict[str, Any]) -> None:
        try:
            uid = str(data.get("user_id") or "").strip()
            if not uid or not self.behavior_store:
                return
            gid_raw = data.get("group_id")
            gid: Optional[str] = str(gid_raw).strip() if gid_raw not in (None, "") else None
            self.behavior_store.patch_session_task(
                uid,
                gid,
                {
                    "last_tool": str(data.get("tool_name") or ""),
                    "last_tool_ok": data.get("tool_ok"),
                    "last_tool_error": str(data.get("tool_error") or "")[:800],
                },
            )
        except Exception as e:
            logger.debug("brain.tool_finished handler: %s", e)

    async def _dialogue_compact_llm_apply(self, pending: DialogueCompactPending) -> None:
        try:
            from core.dialogue_compactor import compact_overflow_with_llm

            para = await compact_overflow_with_llm(
                prev_summary=pending["summary_before"],
                overflow_messages=pending["overflow_messages"],
            )
        except Exception as e:
            logger.debug("dialogue compact apply: %s", e)
            return
        if not (para or "").strip():
            return
        uid = pending["user_id"]
        gid = pending.get("group_id")
        max_sum = max(400, int(pending["max_summary"]))
        expected = pending["summary_after_snippet"]
        before = pending["summary_before"]
        merged = f"{before}\n{para.strip()}".strip() if before else para.strip()
        if len(merged) > max_sum:
            merged = merged[-max_sum:]
        try:
            rec = self.behavior_store.load(uid, gid)
            cur = str(rec.get("dialogue_summary") or "")
            if cur == expected:
                rec["dialogue_summary"] = merged
                self.behavior_store.save(uid, gid, rec)
        except Exception as e:
            logger.debug("dialogue compact save: %s", e)

    def _policy_context(self, user_id: Optional[str], group_id: Optional[str]) -> Dict[str, Any]:
        return {
            "all_module_names": list(self.plugin_registry.loaded_modules.keys()),
            "user_id": user_id,
            "group_id": group_id,
        }

    def _allowed_module_keys(self, user_id: Optional[str], group_id: Optional[str]) -> set:
        ctx = self._policy_context(user_id, group_id)
        allowed_list = self.policy_engine.get_allowed_modules(Role.USER, ctx)
        loaded = set(self.plugin_registry.loaded_modules.keys())
        result = set(allowed_list) & loaded
        if not result and loaded:
            result = loaded
        if self._resilience.is_enabled() and self._resilience.is_safe_mode():
            allow = self._resilience.safe_mode_allowlist()
            result = result & allow if result else loaded & allow
            if not result:
                result = loaded & allow
        hints = self._host_adaptation_hints
        if hints and (
            "prefer_minimal_modules" in hints or "defer_heavy_rag_and_vision" in hints
        ):
            raw = os.getenv(
                "HEAVY_MODULES_UNDER_PRESSURE",
                "rag,books_rag,vision_describe,vision_ocr",
            )
            heavy = {x.strip() for x in raw.split(",") if x.strip()}
            # Диалог нельзя считать «тяжёлым» для вычитания — иначе остаётся echo/math и весь обычный текст идёт в __fallback__.
            for _keep in ("chat-orchestrator", "chat_orchestrator", "smartchat"):
                heavy.discard(_keep)
            before_trim = set(result)
            result -= heavy
            if not result:
                # Имена — как в manifest.name (chat-orchestrator, не chat_orchestrator)
                minimal = {x for x in ("echo", "math", "chat-orchestrator", "smartchat") if x in loaded}
                result = minimal if minimal else loaded - heavy
            else:
                dialog_in_loaded = {
                    k for k in ("chat-orchestrator", "chat_orchestrator", "smartchat") if k in loaded
                }
                if dialog_in_loaded and before_trim & dialog_in_loaded and not (result & dialog_in_loaded):
                    result |= before_trim & dialog_in_loaded
        # Safe-mode allowlist / сужение политики без диалога → весь обычный текст уходит в __fallback__.
        dialog_order = ("chat-orchestrator", "chat_orchestrator", "smartchat")
        loaded_dialog = {k for k in dialog_order if k in loaded}
        if loaded_dialog and not (result & loaded_dialog):
            behavior = self.policy_engine.policies.get("module_behavior", {}).get("default", {})
            whitelist = behavior.get("allowed_modules") or []
            if not whitelist:
                for k in dialog_order:
                    if k in loaded_dialog:
                        result.add(k)
                        break
            else:
                wl = set(whitelist)
                for k in dialog_order:
                    if k in loaded_dialog and k in wl:
                        result.add(k)
                        break
        result = self.plugin_controller.filter_module_keys(result)
        return result

    def get_system_info(self) -> Dict[str, Any]:
        state = self.plugin_registry.get_system_state()
        modules_info: List[Dict[str, str]] = []
        failed = 0
        healthy = 0
        for m in state.modules:
            modules_info.append({"name": m.name, "type": m.type, "status": m.status})
            if m.status == "failed":
                failed += 1
            elif m.status == "healthy":
                healthy += 1
        if failed == 0:
            overall = "healthy"
        elif healthy > 0:
            overall = "degraded"
        else:
            overall = "failed"
        return {
            "overall_status": overall,
            "modules": modules_info,
            "mode": state.mode,
            "anti_flood": {
                "enabled": self.anti_flood_enabled,
                "max_msg_per_10s": self.max_msg_per_10s,
                "max_same_text": self.max_same_text,
                "max_cmd_per_10s": self.max_cmd_per_10s,
                "group_cooldown_sec": self.group_cooldown_sec,
            },
            "monitoring": MONITOR.snapshot(),
            "observability": OBS.snapshot(),
            "config": self._config.as_dict(),
            "planner": {
                "engine": "unified_planner_v1",
            },
            "behavior_engine": {"enabled": True},
            "knowledge_engine": {
                # Строки во внутреннем пуле после последнего ingest_context_sources (не архив и не «всё Mem0»).
                "context_pool_rows": len(self._knowledge_engine.sources),
                "entries": len(self._knowledge_engine.sources),
            },
            "predictive_behavior_engine": {"enabled": bool(self._config.predictive_behavior_enabled)},
            "goal_engine": {"enabled": bool(self._config.goal_engine_enabled)},
            "self_maintenance": {"enabled": bool(self._config.self_maintenance_enabled), "last_run_ts": self._maintenance.last_run_ts},
            "advisor": {"enabled": bool(self._config.self_improvement_advisor_enabled)},
            "resilience": self._resilience.snapshot(),
            "recovery_autonomy": self._recovery_autonomy.snapshot(),
            "host_resources": {
                k: v
                for k, v in get_host_resource_snapshot().items()
                if k in ("available", "cpu_percent", "pressure", "adaptation_hints", "error")
            },
            "host_adaptation_hints": list(self._host_adaptation_hints),
        }

    def assess_flood_risk(
        self,
        *,
        user_id: Optional[str],
        chat_id: Optional[str],
        text: str,
        is_group: bool,
        is_command: bool,
        is_bot_trigger_event: bool,
    ) -> Dict[str, Any]:
        """
        Anti-flood guard (input gating only). Does not touch routing decisions.
        """
        if not self.anti_flood_enabled or not user_id:
            return {"blocked": False, "silent": False, "reason": "", "message": ""}
        now = time.monotonic()
        safe_text = (text or "").strip().lower()
        ukey = f"{chat_id or 'dm'}:{user_id}"
        ckey = str(chat_id or "dm")
        uev = self._flood_user_events[ukey]
        cev = self._flood_chat_events[ckey]

        uev.append({"ts": now, "text": safe_text, "cmd": bool(is_command)})
        cev.append({"ts": now, "uid": user_id, "cmd": bool(is_command)})

        win_start = now - 10.0
        recent_user = [x for x in uev if x["ts"] >= win_start]
        recent_chat = [x for x in cev if x["ts"] >= win_start]
        user_msg_10s = len(recent_user)
        user_cmd_10s = sum(1 for x in recent_user if x.get("cmd"))
        same_text_count = sum(1 for x in recent_user if safe_text and x.get("text") == safe_text)

        blocked = False
        silent = False
        reason = ""

        if user_msg_10s > self.max_msg_per_10s:
            blocked = True
            reason = "rate_limit_user_10s"
        elif same_text_count > self.max_same_text:
            blocked = True
            reason = "repeat_text_spam"
        elif user_cmd_10s > self.max_cmd_per_10s:
            blocked = True
            reason = "command_spam"

        # Group trigger cooldown (mentions/replies/commands only)
        if is_group and is_bot_trigger_event and self.group_cooldown_sec > 0:
            prev = self._group_trigger_ts.get(ukey, 0.0)
            if prev > 0 and (now - prev) < self.group_cooldown_sec:
                blocked = True
                if not reason:
                    reason = "group_trigger_cooldown"
            self._group_trigger_ts[ukey] = now  # always update timestamp

        if blocked:
            if user_msg_10s >= self.max_msg_per_10s * self.hard_flood_multiplier:
                silent = True
            # throttle warning message emission
            warn_allowed = (now - self._flood_warn_ts.get(ukey, 0.0)) > 3.0
            if warn_allowed:
                self._flood_warn_ts[ukey] = now
            # При «жёстком» флуде раньше не было ответа вообще — пользователь думал, что бот мёртв
            if silent and warn_allowed:
                message = "⏳ Слишком много сообщений подряд. Подождите немного."
            elif silent or not warn_allowed:
                message = ""
            else:
                message = self.anti_flood_response
            return {
                "blocked": True,
                "silent": silent,
                "reason": reason,
                "message": message,
                "stats": {
                    "user_msg_10s": user_msg_10s,
                    "user_cmd_10s": user_cmd_10s,
                    "same_text_count": same_text_count,
                    "chat_msg_10s": len(recent_chat),
                },
            }
        return {"blocked": False, "silent": False, "reason": "", "message": ""}

    def _assemble_brain_context(
        self,
        user_id: Optional[str],
        group_id: Optional[str],
        persisted: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build BrainContext contract used by module execution.
        The contract is backward compatible: existing fields stay unchanged.
        """
        persisted = persisted or {}
        ds0 = dict(persisted.get("dialogue_state") or {})
        gc0 = dict(persisted.get("group_context") or {})
        recent = persisted.get("recent_messages") or []
        if not isinstance(recent, list):
            recent = []
        recent = compress_recent_dialogue(recent)
        try:
            from core.context_tool_trim import trim_tool_outputs_in_dialogue

            recent = trim_tool_outputs_in_dialogue(recent)
        except Exception as e:
            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        topic_tracking = persisted.get("topic_tracking") or {}
        if not isinstance(topic_tracking, dict):
            topic_tracking = {}
        last_micro = persisted.get("last_micro_emotion") or {}
        if not isinstance(last_micro, dict):
            last_micro = {}
        style_anchor = persisted.get("persona_style_anchor") or {}
        if not isinstance(style_anchor, dict):
            style_anchor = {}
        user_facts = persisted.get("user_facts") or {}
        if not isinstance(user_facts, dict):
            user_facts = {}
        user_facts_meta = persisted.get("user_facts_meta") or {}
        if not isinstance(user_facts_meta, dict):
            user_facts_meta = {}
        if user_id:
            try:
                from core.user_facts import brain_user_facts_from_store

                _bf, _bm = brain_user_facts_from_store(self.behavior_store, str(user_id), group_id)
                if _bf:
                    user_facts = _bf
                if _bm:
                    user_facts_meta = _bm
            except Exception as e:
                logger.debug("assemble_brain_user_facts: %s", e)

        group_memory_max = 10
        if group_id:
            try:
                group_memory_max = int(load_group_chat_policy().get("group_memory_max") or 12)
            except Exception:
                group_memory_max = 12
            group_memory_max = max(4, min(40, group_memory_max))
        context: Dict[str, Any] = {
            "user_id": user_id,
            "group_id": group_id,
            "mem0_facts": [],
            "persona": {},
            "psychology": {},
            "digital_twin": {},
            "persona_teacher_addon": "",
            "session_first_user_text": str(persisted.get("session_first_user_text") or "").strip(),
            "telegram_commands_catalog": "",
            "routing_prefs_hint": "",
            "operator_rules_brain_addon": "",
            "ephemeral_lessons_brain_addon": "",
            "dialogue_state": {
                "turn_index": int(ds0.get("turn_index", 0)),
                "mode": ds0.get("mode", "chat"),
                "last_intent": ds0.get("last_intent", "unknown"),
            },
            # ── Prewarm FIX: detect new session for cold-start optimization ──
            "session_is_new": int(ds0.get("turn_index", 0)) <= 1,
            "group_context": gc0,
            "recent_dialogue": recent[-group_memory_max:],
            "dialogue_summary": compress_dialogue_summary(str(persisted.get("dialogue_summary") or "").strip()),
            "topic_tracking": topic_tracking,
            "behavior_engine": {
                "persisted": True,
                "last_micro_emotion": last_micro,
                "persona_style_anchor": style_anchor,
            },
            "user_facts": user_facts,
            "user_facts_meta": user_facts_meta,
            "facts_confirmation_pending": persisted.get("pending_facts_confirmation") or {},
            "conversation_style": str(persisted.get("conversation_style") or "balanced").strip().lower(),
            "thinking_markers": {
                "enabled": True,
                "style": "lightweight",
            },
            "typing_hooks": {
                "enabled": True,
                "phase": "idle",
            },
        }
        _st = persisted.get("session_task")
        if isinstance(_st, dict):
            context["session_task"] = dict(_st)

        if not user_id:
            return context

        try:
            if self.persona_engine and hasattr(self.persona_engine, "get_persona"):
                persona = self.persona_engine.get_persona(user_id) or {}
                if isinstance(persona, dict):
                    context["persona"] = persona
                    if persona.get("persona") == "teacher_mode":
                        context["persona_teacher_addon"] = (
                            "Режим учителя: давай проверяемые определения и при необходимости вызывай "
                            "инструменты Wikipedia.scan или UniversalSearch.search для проверки фактов, "
                            "прежде чем утверждать учебный материал."
                        )
        except Exception as e:
            logger.debug("context persona load failed: %s", e)

        try:
            if self.psychology_engine and hasattr(self.psychology_engine, "get_psychology_profile"):
                profile = self.psychology_engine.get_psychology_profile(user_id) or {}
                if isinstance(profile, dict):
                    context["psychology"] = profile
        except Exception as e:
            logger.debug("context psychology load failed: %s", e)

        try:
            if self.digital_twin and hasattr(self.digital_twin, "get_digital_twin"):
                twin = self.digital_twin.get_digital_twin(user_id) or {}
                if isinstance(twin, dict):
                    context["digital_twin"] = twin
        except Exception as e:
            logger.debug("context digital twin load failed: %s", e)

        if group_id and self.group_behavior and hasattr(self.group_behavior, "get_group_behavior"):
            try:
                live = self.group_behavior.get_group_behavior(group_id)
                if isinstance(live, dict) and live:
                    gc = dict(context.get("group_context") or {})
                    gc["live_behavior"] = live
                    context["group_context"] = gc
            except Exception as e:
                logger.debug("group_behavior context: %s", e)

        if group_id:
            try:
                from core.group_transcript import get_brain_extras

                gx = get_brain_extras(group_id)
                context["group_transcript_compact"] = gx.get("transcript_compact") or ""
                context["group_commitments_hint"] = gx.get("commitments_hint") or ""
                context["group_roster_hint"] = gx.get("roster_hint") or ""
            except Exception as e:
                logger.debug("group_transcript extras: %s", e)
                context["group_transcript_compact"] = ""
                context["group_commitments_hint"] = ""
                context["group_roster_hint"] = ""

        context["blended_style_stable"] = _stable_blend_style(
            context.get("persona") or {},
            context.get("psychology") or {},
            context.get("digital_twin") or {},
            style_anchor,
        )

        rp = persisted.get("routing_prefs") if isinstance(persisted.get("routing_prefs"), dict) else {}
        if rp.get("prefer_general_over_math"):
            context["routing_prefs_hint"] = (
                "Пользователь просил не навязывать калькулятор (/calc), если нет явной математики. "
                "Ссылки t.me/…, приглашения в группы и обычный текст — не повод предлагать /calc."
            )

        op_add = brain_context_addon_from_file()
        sd_add = ""
        try:
            from core.system_directive_addon import load_system_directive_brain_addon

            sd_add = load_system_directive_brain_addon()
        except Exception as e:
            logger.debug("system_directive_addon: %s", e)
        merged_rules = "\n\n".join(x for x in (op_add, sd_add) if x and str(x).strip())
        if merged_rules:
            context["operator_rules_brain_addon"] = merged_rules

        try:
            from core.command_catalog import format_brain_telegram_command_catalog

            context["telegram_commands_catalog_min"] = format_brain_telegram_command_catalog(
                self.plugin_registry, tier="minimal", max_chars=4000
            )
            context["telegram_commands_catalog_full"] = format_brain_telegram_command_catalog(
                self.plugin_registry, tier="full", max_chars=14_000, max_module_commands=160
            )
            # По умолчанию в контракте — короткий каталог; полный подмешивает pipeline при необходимости.
            context["telegram_commands_catalog"] = context["telegram_commands_catalog_min"]
        except Exception as e:
            logger.debug("telegram_commands_catalog: %s", e)

        try:
            context["plugin_manifest_prompts"] = self.plugin_controller.format_manifest_prompts_for_brain()
        except Exception as e:
            logger.debug("plugin_manifest_prompts: %s", e)

        try:
            from core.daily_highlights import recent_highlights_hint

            dh = recent_highlights_hint(limit_days=3, max_notes=8)
            if dh:
                context["daily_highlights_hint"] = dh
        except Exception as e:
            logger.debug("daily_highlights_hint: %s", e)

        return context

    def _sanitize_payload(self, payload: Any) -> str:
        """Universal input sanitation for text payloads."""
        if payload is None:
            return ""
        if not isinstance(payload, str):
            payload = str(payload)
        # Normalize control chars while preserving newlines/tabs.
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        payload = "".join(ch for ch in payload if ch >= " " or ch in "\n\t")
        return payload.strip()

    def _normalize_input_data(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Safe payload normalization without changing input schema."""
        normalized = dict(input_data or {})
        normalized["payload"] = self._sanitize_payload(normalized.get("payload", ""))
        return normalized

    def _build_situation_for_context(self, *, input_meta: Dict[str, Any], maintenance_ran: bool) -> Dict[str, Any]:
        """Лёгкий снимок «состояния системы» на один ход — для модулей/skills в args.context (без тяжёлого get_system_info)."""
        meta = input_meta if isinstance(input_meta, dict) else {}
        tid = str(meta.get("trace_id") or "")
        safe_degraded = bool(self._resilience.is_enabled() and self._resilience.is_safe_mode())
        state = self.plugin_registry.get_system_state()
        failed = sum(1 for m in state.modules if m.status == "failed")
        healthy = sum(1 for m in state.modules if m.status == "healthy")
        if failed == 0:
            overall = "healthy"
        elif healthy > 0:
            overall = "degraded"
        else:
            overall = "failed"

        def _clip(val: Any, n: int = 200) -> Optional[str]:
            t = str(val or "").strip()
            if not t:
                return None
            return t[:n] + ("…" if len(t) > n else "")

        sm: Dict[str, Any] = {}
        rr: Dict[str, Any] = {}
        if self._resilience.is_enabled():
            snap = self._resilience.snapshot()
            sm = snap.get("safe_mode") or {}
            if not isinstance(sm, dict):
                sm = {}
            rr = snap.get("restart_requested") or {}
            if not isinstance(rr, dict):
                rr = {}

        return {
            "schema": "situation_v1",
            "trace_id": tid,
            "maintenance_ran": maintenance_ran,
            "plan_mode": "degraded" if safe_degraded else "full",
            "modules_overall": overall,
            "modules_failed": failed,
            "resilience": {
                "enabled": self._resilience.is_enabled(),
                "safe_mode_active": bool(sm.get("active")),
                "safe_mode_reason": _clip(sm.get("reason")),
                "restart_requested": bool(rr.get("requested")),
                "restart_reason": _clip(rr.get("reason")),
            },
            "efficiency_block": build_efficiency_snapshot(days=7.0, orchestrator=self),
        }

    def _build_step_context(
        self,
        *,
        user_id: Optional[str],
        group_id: Optional[str],
        normalized_input: Dict[str, Any],
        persisted: Dict[str, Any],
        decision,
        planned_module: str,
        planned_intent: str,
        text: str,
        file_context: Optional[Dict[str, Any]],
        doc_context: Optional[Dict[str, Any]],
        code_context: Optional[Dict[str, Any]],
        facts_flow: Dict[str, Any],
        knowledge_hint: Dict[str, Any],
        predictive_hint: Dict[str, Any],
        goal_hints: Dict[str, Any],
        cached_brain_context: Optional[Dict[str, Any]] = None,
        maintenance_ran: bool = False,
        lookahead_plan: Optional[Dict[str, Any]] = None,
        cost_patch: Optional[Dict[str, Any]] = None,
        efficiency_patch: Optional[Dict[str, Any]] = None,
        scenario_forecast: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if cached_brain_context is not None:
            ctx = copy.deepcopy(cached_brain_context)
        else:
            ctx = self._assemble_brain_context(user_id, group_id, persisted=persisted)
        # behavior_store holds threading.Lock — never put it in cached_brain_context for deepcopy
        ctx["_behavior_store"] = self.behavior_store
        try:
            from core.behavior_store import topic_tracking_for_turn

            ctx["topic_tracking"] = topic_tracking_for_turn(text, ctx.get("topic_tracking"))
        except Exception as e:
            logger.debug("topic_tracking_for_turn: %s", e)
        behavior_policy = self._behavior_engine.derive_policy(
            persona=ctx.get("persona") or {},
            psychology=ctx.get("psychology") or {},
            user_facts=ctx.get("user_facts") or {},
            dialogue_state=ctx.get("dialogue_state") or {},
        )
        try:
            if recent_dialogue_forbids_service_clarifications(ctx.get("recent_dialogue") or []):
                behavior_policy["no_service_clarifications"] = True
        except Exception as e:
            logger.debug("no_service_clarifications: %s", e)
        try:
            from core.lexical_dialog_recall import build_lexical_recall_hint

            _lx = build_lexical_recall_hint(
                str(user_id or ""),
                group_id,
                text,
                recent_dialogue=ctx.get("recent_dialogue"),
            )
            if _lx:
                ctx["lexical_dialog_recall_hint"] = _lx
                _prev_rp = str(ctx.get("routing_prefs_hint") or "").strip()
                ctx["routing_prefs_hint"] = f"{_prev_rp}\n\n{_lx}".strip() if _prev_rp else _lx
        except Exception as e:
            logger.debug("lexical_dialog_recall: %s", e)
        if predictive_hint.get("terse_mode"):
            behavior_policy["verbosity"] = "concise"
        _cost = cost_patch if isinstance(cost_patch, dict) else {}
        _eff = efficiency_patch if isinstance(efficiency_patch, dict) else {}
        _force_verbosity = str(_cost.get("force_verbosity") or "").strip().lower()
        if _force_verbosity in {"concise", "balanced", "rich", "structured"}:
            behavior_policy["verbosity"] = _force_verbosity
        _eff_verbosity = str(_eff.get("force_verbosity") or "").strip().lower()
        if _eff_verbosity in {"concise", "balanced", "rich", "structured"}:
            behavior_policy["verbosity"] = _eff_verbosity
        skill_priority = predictive_hint.get("skill_priority")
        if isinstance(skill_priority, list) and skill_priority:
            behavior_policy["skill_priority"] = skill_priority
        if isinstance(goal_hints.get("goal_ids"), list) and goal_hints.get("goal_ids"):
            behavior_policy["goal_focus"] = goal_hints.get("goal_ids")
        try:
            from core.autonomic_fatigue import apply_fatigue_to_policy

            behavior_policy, _fat_slim = apply_fatigue_to_policy(behavior_policy)
            if _fat_slim:
                ctx["fatigue_force_slim"] = True
        except Exception as e:
            logger.debug("autonomic_fatigue: %s", e)
        unified = self._context_builder.build(
            user_id=user_id,
            group_id=group_id,
            input_meta=normalized_input.get("meta") or {},
            persisted={**persisted, "recent_messages": ctx.get("recent_dialogue") or []},
            persona=ctx.get("persona") or {},
            psychology=ctx.get("psychology") or {},
            digital_twin=ctx.get("digital_twin") or {},
            behavior_policy=behavior_policy,
            knowledge_hint=knowledge_hint,
            predictive_hint=predictive_hint,
            goal_hints=goal_hints,
        )
        ctx["unified_context"] = unified
        ctx["behavior_policy"] = behavior_policy
        ctx["knowledge_hint"] = knowledge_hint
        ctx["predictive_hint"] = predictive_hint
        ctx["goal_hints"] = goal_hints
        if isinstance(persisted.get("cdc_policy"), dict):
            ctx["cdc_policy"] = dict(persisted.get("cdc_policy") or {})
        if isinstance(persisted.get("affect_state"), dict):
            ctx["affect_state"] = dict(persisted.get("affect_state") or {})
        if isinstance(persisted.get("self_model"), dict):
            ctx["self_model"] = dict(persisted.get("self_model") or {})
        dialogue_state = dict(persisted.get("dialogue_state") or {})
        dialogue_state.update(dict(ctx.get("dialogue_state") or {}))
        _prev_tier_raw = str(dialogue_state.get("task_tier") or "").strip()
        _prev_tier = _prev_tier_raw or None
        dialogue_state["last_intent"] = planned_intent
        dialogue_state["planned_module"] = planned_module
        dialogue_state["has_payload"] = bool(text)
        dialogue_state["planner_reason"] = decision.reason
        try:
            from core.task_depth import infer_task_tier_with_history

            dialogue_state["task_tier"] = infer_task_tier_with_history(
                text,
                ctx.get("recent_dialogue"),
                max_user_turns=4,
                previous_tier=_prev_tier,
                planned_intent=planned_intent,
                terse_mode=bool(predictive_hint.get("terse_mode")),
            )
        except Exception:
            dialogue_state["task_tier"] = "shallow"
        try:
            from core.affect_state import modulate_task_tier_with_affect

            dialogue_state["task_tier"] = modulate_task_tier_with_affect(
                str(dialogue_state.get("task_tier") or "shallow"),
                persisted.get("affect_state") if isinstance(persisted, dict) else None,
            )
        except Exception as e:
            logger.debug("affect tier modulation: %s", e)
        try:
            from core.cdc import apply_route_tier_cap

            dialogue_state["task_tier"] = apply_route_tier_cap(
                str(dialogue_state.get("task_tier") or "shallow"),
                planned_module=planned_module,
                planned_intent=planned_intent,
                persisted=persisted,
            )
        except Exception as e:
            logger.debug("cdc tier cap: %s", e)
        try:
            from core.task_depth import apply_tier_ceiling

            _ceiling = str(_cost.get("task_tier_ceiling") or "").strip()
            if _ceiling:
                dialogue_state["task_tier"] = apply_tier_ceiling(
                    str(dialogue_state.get("task_tier") or "shallow"),
                    _ceiling,
                )
            _eff_ceiling = str(_eff.get("task_tier_ceiling") or "").strip()
            if _eff_ceiling:
                dialogue_state["task_tier"] = apply_tier_ceiling(
                    str(dialogue_state.get("task_tier") or "shallow"),
                    _eff_ceiling,
                )
        except Exception as e:
            logger.debug("cost autopilot tier cap: %s", e)
        ctx["dialogue_state"] = dialogue_state
        if _cost:
            ctx["cost_autopilot"] = dict(_cost)
            if _cost.get("disable_tools"):
                ctx["brain_disable_tools"] = True
        if _eff:
            ctx["efficiency_guard"] = dict(_eff)
            if _eff.get("disable_tools_for_general") and planned_intent == "general":
                ctx["brain_disable_tools"] = True
        if decision.skill_name:
            ctx["planner_skill_hint"] = decision.skill_name
        ctx["planner_skill_name"] = str(decision.skill_name or "")
        _intent_norm = (planned_intent or "").strip().lower()
        if _intent_norm in {"test", "reasoning"}:
            ctx["planner_mode_guard"] = {
                "mode": "TEST_MODE" if _intent_norm == "test" else "REASONING_MODE",
                "no_drift": True,
                "strict_split": True,
            }
        if isinstance(file_context, dict) and file_context:
            ctx["file_context"] = file_context
        if isinstance(doc_context, dict) and doc_context:
            ctx["document_intake"] = doc_context
        if isinstance(code_context, dict) and code_context:
            ctx["code_intake"] = code_context
        if facts_flow:
            ctx["facts_flow"] = facts_flow
            ff = facts_flow.get("facts")
            if isinstance(ff, dict):
                ctx["user_facts"] = ff
            fm = facts_flow.get("facts_meta")
            if isinstance(fm, dict):
                ctx["user_facts_meta"] = fm
        if scenario_forecast is not None:
            try:
                from core.scenario_engine import build_brain_scenario_addon

                ctx["scenario_forecast"] = scenario_forecast.to_dict()
                _addon = build_brain_scenario_addon(scenario_forecast)
                if _addon:
                    ctx["scenario_brain_addon"] = _addon
                if getattr(scenario_forecast, "prefer_news_direct", False):
                    try:
                        from core.brain_own_turn import planner_direct_allowed

                        if planner_direct_allowed("news"):
                            ctx["brain_prefer_news_direct"] = True
                    except Exception:
                        ctx["brain_prefer_news_direct"] = True
                _lane = str(getattr(scenario_forecast, "situation_lane", "") or "").strip()
                if _lane:
                    ctx["situation_lane"] = _lane
            except Exception as e:
                logger.debug("scenario_forecast ctx: %s", e)
        _meta_for_fast = normalized_input.get("meta") if isinstance(normalized_input.get("meta"), dict) else {}
        _telegram_reply_for_prompt = bool(str(_meta_for_fast.get("telegram_reply_context") or "").strip())
        if (
            brain_fast_chitchat_eligible(text, group_id, file_context, doc_context, code_context)
            and not _telegram_reply_for_prompt
            and not private_dm_chitchat_continuity_override(
                group_id, persisted.get("dialogue_state"), text
            )
        ):
            _chitchat_fast_ok = True
            try:
                from core.heuristic_context_gate import should_run_shortcut

                _gr_ch = should_run_shortcut(
                    "chitchat_fast_eligible",
                    text,
                    persisted=persisted,
                    planner_context=ctx,
                )
                _chitchat_fast_ok = _gr_ch.allowed
            except Exception as e:
                logger.debug("chitchat_fast gate: %s", e)
            if _chitchat_fast_ok:
                ctx["brain_fast_chitchat"] = True
                if _env_truthy("BRAIN_FAST_CHITCHAT_SKIP_MEM0", True):
                    ctx["brain_skip_mem0_lookup"] = True
                    ctx["brain_skip_memory_fetch"] = True
                    ctx["memory_managed"] = True
                    ctx["mem0_facts"] = []
        gshot = (normalized_input.get("meta") or {}).get("group_chat_snapshot")
        if isinstance(gshot, dict) and group_id:
            ctx["group_chat_snapshot"] = gshot
        _meta_in = normalized_input.get("meta") or {}
        if isinstance(_meta_in, dict) and "telegram_is_admin" in _meta_in:
            ctx["telegram_is_admin"] = bool(_meta_in.get("telegram_is_admin"))
        if isinstance(_meta_in, dict):
            if _meta_in.get("telegram_message_date_unix") is not None:
                ctx["telegram_message_date_unix"] = _meta_in.get("telegram_message_date_unix")
            if _meta_in.get("telegram_message_date_iso"):
                ctx["telegram_message_date_iso"] = str(_meta_in.get("telegram_message_date_iso"))
            ctx["has_telegram_attachment"] = bool(_meta_in.get("has_telegram_attachment"))
            if _meta_in.get("telegram_document_filename"):
                ctx["telegram_document_filename"] = str(_meta_in.get("telegram_document_filename"))
            pd = str(_meta_in.get("pending_doc_id") or "").strip()
            if pd:
                ctx["pending_doc_id"] = pd
            trc = str(_meta_in.get("telegram_reply_context") or "").strip()
            if trc:
                ctx["telegram_reply_context"] = trc
            if _meta_in.get("telegram_has_forward"):
                ctx["telegram_has_forward"] = True
            if _meta_in.get("telegram_voice_transcription"):
                ctx["telegram_voice_transcription"] = True
            tl0 = _meta_in.get("telegram_location")
            if isinstance(tl0, dict) and tl0.get("latitude") is not None and tl0.get("longitude") is not None:
                ctx["telegram_location"] = dict(tl0)
        ep = brain_addon_for_text(text)
        if ep:
            ctx["ephemeral_lessons_brain_addon"] = ep
        ctx["situation"] = self._build_situation_for_context(
            input_meta=normalized_input.get("meta") or {},
            maintenance_ran=maintenance_ran,
        )
        _task_tier = str(dialogue_state.get("task_tier") or "shallow").strip() or "shallow"
        _stable_cache_shallow = _env_truthy("OPENROUTER_CACHE_STABLE_HINTS_FOR_SHALLOW", True)
        _suppress_dynamic_hints = _stable_cache_shallow and _task_tier == "shallow"
        try:
            from core.experience_memory import build_hint_for_context, experience_enabled

            if (
                experience_enabled()
                and not bool(_cost.get("disable_experience_hint"))
                and not _suppress_dynamic_hints
            ):
                _eh = build_hint_for_context(
                    user_text=text,
                    intent=planned_intent,
                    module=planned_module,
                    decision=decision,
                    predictive_hint=predictive_hint,
                )
                if _eh:
                    ctx["experience_memory_hint"] = _eh
        except Exception as e:
            logger.debug("experience_memory hint: %s", e)
        try:
            from core.route_risk_memory import build_route_risk_hint, route_risk_hint_enabled

            if (
                route_risk_hint_enabled()
                and not bool(_cost.get("disable_route_risk_hint"))
                and not _suppress_dynamic_hints
            ):
                _rrh = build_route_risk_hint(user_text=text, intent=planned_intent)
                if _rrh:
                    ctx["route_risk_hint"] = _rrh
        except Exception as e:
            logger.debug("route_risk hint: %s", e)
        try:
            from core.dialogue_feedback_signals import build_user_remark_hint

            if not _suppress_dynamic_hints:
                _urh = build_user_remark_hint(
                    user_text=text,
                    routing_prefs=persisted.get("routing_prefs") if isinstance(persisted.get("routing_prefs"), dict) else {},
                )
                if _urh:
                    ctx["user_remark_hint"] = _urh
        except Exception as e:
            logger.debug("user_remark hint: %s", e)
        try:
            from core.dialogue_feedback_signals import (
                merge_recent_remarks_into_routing_prefs,
                user_feedback_likely,
            )
            from core.user_correction_bus import record_user_correction_turn

            if user_feedback_likely(text):
                rp = persisted.get("routing_prefs") if isinstance(persisted.get("routing_prefs"), dict) else {}
                rp = merge_recent_remarks_into_routing_prefs(dict(rp), text)
                persisted["routing_prefs"] = rp
                try:
                    self.behavior_store.save(str(user_id), group_id, persisted)
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                _cb = record_user_correction_turn(
                    user_id=str(user_id),
                    user_text=text,
                    behavior_store=self.behavior_store,
                    group_id=group_id,
                    correction_text=text,
                    source="dialogue_feedback",
                )
                if _cb.get("applied"):
                    ctx["_correction_bus_applied"] = list(_cb["applied"])
        except Exception as e:
            logger.debug("user_correction_bus: %s", e)
        try:
            from core.strategy_path_memory import build_strategy_path_hint, strategy_path_enabled

            if (
                strategy_path_enabled()
                and not bool(_cost.get("disable_strategy_hint"))
                and not _suppress_dynamic_hints
            ):
                _sth = build_strategy_path_hint(
                    user_text=text,
                    intent=planned_intent,
                    task_tier=_task_tier,
                )
                if _sth:
                    ctx["strategy_path_hint"] = _sth
        except Exception as e:
            logger.debug("strategy_path hint: %s", e)
        if isinstance(lookahead_plan, dict) and lookahead_plan:
            ctx["lookahead_plan"] = lookahead_plan
        try:
            from core.self_model import hydrate_autonomy_goal_from_runtime

            hydrate_autonomy_goal_from_runtime(
                ctx,
                user_text=text,
                goal_hints=goal_hints if isinstance(goal_hints, dict) else {},
                lookahead_plan=lookahead_plan if isinstance(lookahead_plan, dict) else None,
                planned_intent=planned_intent,
                task_tier=_task_tier,
            )
        except Exception as e:
            logger.debug("hydrate_autonomy_goal_from_runtime: %s", e)
        try:
            from core.message_archive import maybe_backfill_context_recent_dialogue

            maybe_backfill_context_recent_dialogue(
                ctx,
                user_id=user_id,
                group_id=group_id,
                user_text=text,
                input_meta=normalized_input.get("meta") if isinstance(normalized_input.get("meta"), dict) else {},
            )
        except Exception as e:
            logger.debug("dialogue archive backfill: %s", e)
        try:
            from core.operator_truth_signals import maybe_attach_operator_truth_signals

            maybe_attach_operator_truth_signals(
                ctx,
                orchestrator=self,
                user_text=text,
                is_admin=bool(ctx.get("telegram_is_admin")),
            )
        except Exception as e:
            logger.debug("operator_truth_signals: %s", e)
        return ctx

    def plan(self, input_data: Input, user_id: str = None, group_id: str = None) -> Plan:
        MONITOR.inc("plan_calls")
        normalized_input = self._normalize_input_data(input_data.model_dump())
        text = normalized_input.get("payload", "")
        input_meta = normalized_input.get("meta") or {}
        try:
            from core.request_context import ensure_request_id

            if isinstance(input_meta, dict):
                rid = str(input_meta.get("request_id") or input_meta.get("relay_request_id") or "").strip()
                ensure_request_id(rid or None)
        except Exception:
            pass
        file_context = input_meta.get("file_context") if isinstance(input_meta, dict) else None
        doc_context = input_meta.get("document_intake") if isinstance(input_meta, dict) else None
        code_context = input_meta.get("code_intake") if isinstance(input_meta, dict) else None

        logger.info(f"[PLAN] input from={user_id} text={text!r}")
        _obs_mark(input_meta, "plan_start")
        text_stripped = self._sanitize_payload(text) if text else ""
        _persisted_plan: Dict[str, Any] = {}
        try:
            from core.brain.text_helpers import normalize_capital_query_typos

            text_stripped = normalize_capital_query_typos(text_stripped)
            if text_stripped != (text or "").strip():
                text = text_stripped
                if isinstance(normalized_input, dict):
                    normalized_input["payload"] = text_stripped
        except Exception as e:
            logger.debug("normalize_capital_query_typos: %s", e)
        _tl_early = (
            input_meta.get("telegram_location") if isinstance(input_meta, dict) else None
        )
        has_telegram_attachment = bool(input_meta.get("has_telegram_attachment"))
        has_rich = (
            (isinstance(file_context, dict) and bool(file_context.get("file_type")))
            or (isinstance(doc_context, dict) and bool(doc_context))
            or (isinstance(code_context, dict) and bool(code_context))
            or has_telegram_attachment
            or isinstance(_tl_early, dict)
        )
        if not text_stripped and not has_rich:
            _obs_mark(input_meta, "plan_context_skip_empty")
            maintenance = self._maintenance.maybe_run(interval_sec=self._maintenance_interval_sec)
            if maintenance.get("ran"):
                MONITOR.inc("maintenance_cycles_total")
                try:
                    hr = get_host_resource_snapshot(force=True)
                    self._host_adaptation_hints = list(hr.get("adaptation_hints") or [])
                except Exception as e:
                    logger.debug("host_resources refresh: %s", e)
                try:
                    self._recovery_autonomy.tick(self, maintenance_ran=True)
                except Exception as e:
                    logger.debug("recovery_autonomy tick: %s", e)
                try:
                    self._resilience.tick(self, maintenance_ran=True)
                except Exception as e:
                    logger.debug("resilience tick: %s", e)
                # Autonomy 3.0: self-optimization analysis
                try:
                    _opt = self_healing_analyze()
                    if _opt.get("suggestions"):
                        logger.info("self_optimization: %s", _opt)
                    from core.memory_store import maintenance as episodic_maintenance
                    episodic_maintenance()
                except Exception as e:
                    logger.debug("self_optimization maintenance: %s", e)
            _obs_mark(input_meta, "plan_maintenance")
            maintenance_ran = bool(maintenance.get("ran"))
            ctx_minimal: Dict[str, Any] = {
                "user_id": user_id,
                "group_id": group_id,
                "dialogue_state": {
                    "last_intent": "empty",
                    "planned_module": "__fallback__",
                    "has_payload": False,
                    "planner_reason": "empty_no_attachment_fast_path",
                },
                "situation": self._build_situation_for_context(
                    input_meta=input_meta if isinstance(input_meta, dict) else {},
                    maintenance_ran=maintenance_ran,
                ),
            }
            steps_fast = [
                PlanStep(
                    module_name="__fallback__",
                    args={
                        "input": normalized_input,
                        "context": ctx_minimal,
                        "fallback_variant": "empty_payload",
                    },
                )
            ]
            tid_empty = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
            record_planner_pulse(
                intent="empty",
                module="__fallback__",
                fallback=True,
                reason="empty_no_attachment_fast_path",
                skill_name="",
                trace_id=str(tid_empty),
                maintenance_ran=maintenance_ran,
                safe_mode=bool(self._resilience.is_enabled() and self._resilience.is_safe_mode()),
            )
            record_usage(text="", intent="empty", module="__fallback__")
            MONITOR.inc("planner_decisions_total")
            if gemma_core_log_full():
                logger.info(
                    "[CORE] planner intent=empty module=__fallback__ fallback=True reason=empty_no_attachment_fast_path "
                    "skill=- trace=%s maintenance_ran=%s safe_mode=%s",
                    tid_empty[:12] if tid_empty else "-",
                    maintenance_ran,
                    bool(self._resilience.is_enabled() and self._resilience.is_safe_mode()),
                    extra={
                        "gemma_event": "planner_decision",
                        "trace_id": tid_empty or None,
                        "intent": "empty",
                        "gemma_module": "__fallback__",
                        "fallback": True,
                        "reason": "empty_no_attachment_fast_path",
                        "skill_name": None,
                    },
                )
            _obs_mark(input_meta, "plan_decide")
            _obs_mark(input_meta, "plan_done")
            if self._resilience.is_enabled() and self._resilience.is_safe_mode():
                return Plan(steps=steps_fast, mode="degraded")
            return Plan(steps=steps_fast, mode="full")

        if user_id and self.behavior_store:
            _persisted_plan = self.behavior_store.load(user_id, group_id)

        if user_id and self.behavior_store:
            _persisted_plan = self.behavior_store.load(user_id, group_id)

        if user_id and text_stripped and not str(text_stripped).lstrip().startswith("/"):
            try:
                from core.pre_llm_plan import try_pre_llm_direct_plan

                _pre_llm = try_pre_llm_direct_plan(
                    user_id=str(user_id),
                    group_id=group_id,
                    text=text_stripped,
                    persisted=_persisted_plan if isinstance(_persisted_plan, dict) else None,
                    input_meta=input_meta if isinstance(input_meta, dict) else None,
                )
                if _pre_llm is not None:
                    _pre_reason, _pre_reply = _pre_llm
                    _tid_pre = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="general",
                        module="__fallback__",
                        fallback=False,
                        reason=_pre_reason,
                        skill_name="",
                        trace_id=str(_tid_pre),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="general", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    MONITOR.inc("pre_llm_plan_direct_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": _pre_reason,
                                    "direct_reply": _pre_reply,
                                },
                            )
                        ],
                        mode="full",
                    )
            except Exception as e:
                logger.debug("pre_llm_direct_plan: %s", e)

        if user_id and text_stripped and not str(text_stripped).lstrip().startswith("/"):
            try:
                from core.reminder_nl import try_cancel_natural_reminder

                _nl_cancel = try_cancel_natural_reminder(str(user_id), text_stripped)
                if _nl_cancel is not None and str(_nl_cancel.get("reply") or "").strip():
                    _reply_c = str(_nl_cancel.get("reply") or "").strip()
                    _tid_c = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="reminder",
                        module="__fallback__",
                        fallback=False,
                        reason="nl_cancel_reminder",
                        skill_name="schedule_helper",
                        trace_id=str(_tid_c),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="reminder", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "nl_cancel_reminder",
                                    "direct_reply": _reply_c,
                                },
                            )
                        ],
                        mode="full",
                    )
            except Exception as e:
                logger.debug("reminder_nl cancel: %s", e)
            try:
                from core.schedule_nl import try_schedule_weekly_nl

                _nl_sched = try_schedule_weekly_nl(str(user_id), text_stripped)
                if _nl_sched is not None and str(_nl_sched.get("reply") or "").strip():
                    _reply_s = str(_nl_sched.get("reply") or "").strip()
                    _tid_s = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="schedule",
                        module="__fallback__",
                        fallback=False,
                        reason="nl_weekly_schedule",
                        skill_name="schedule_helper",
                        trace_id=str(_tid_s),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="schedule", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "nl_weekly_schedule",
                                    "direct_reply": _reply_s,
                                },
                            )
                        ],
                        mode="full",
                    )
            except Exception as e:
                logger.debug("schedule_nl: %s", e)
            try:
                from core.reminder_nl import try_schedule_natural_reminder

                _nl_rem = try_schedule_natural_reminder(str(user_id), text_stripped)
                if _nl_rem is not None:
                    _reply = str(_nl_rem.get("reply") or "").strip()
                    if _reply:
                        logger.info(
                            "[PLAN] nl_reminder uid=%s ok=%s",
                            user_id,
                            bool(_nl_rem.get("ok")),
                        )
                        _tid_rem = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                        record_planner_pulse(
                            intent="reminder",
                            module="__fallback__",
                            fallback=False,
                            reason="nl_reminder" if _nl_rem.get("ok") else "nl_reminder_no_time",
                            skill_name="schedule_helper",
                            trace_id=str(_tid_rem),
                            maintenance_ran=False,
                            safe_mode=bool(
                                self._resilience.is_enabled() and self._resilience.is_safe_mode()
                            ),
                        )
                        record_usage(text=text_stripped, intent="reminder", module="__fallback__")
                        MONITOR.inc("planner_decisions_total")
                        _steps_nl = [
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "nl_reminder",
                                    "direct_reply": _reply,
                                },
                            )
                        ]
                        _obs_mark(input_meta, "plan_done")
                        return Plan(steps=_steps_nl, mode="full")
            except Exception as e:
                logger.debug("nl_reminder plan: %s", e)

        persisted = _persisted_plan if user_id else {}
        if user_id and self.behavior_store and isinstance(persisted, dict):
            try:
                from core.conversation_epoch import maybe_idle_bump_epoch, touch_activity

                idle_reason = maybe_idle_bump_epoch(
                    persisted, user_id=str(user_id), group_id=group_id
                )
                if idle_reason:
                    self.behavior_store.save(user_id, group_id, persisted)
                else:
                    touch_activity(persisted)
            except Exception as e:
                logger.debug("conversation_epoch idle: %s", e)
        _tl_plan = input_meta.get("telegram_location") if isinstance(input_meta, dict) else None
        if user_id and isinstance(_tl_plan, dict):
            try:
                from core.geo_location_reply import try_telegram_location_reply_sync

                _loc_reply = try_telegram_location_reply_sync(
                    text_stripped,
                    meta=input_meta if isinstance(input_meta, dict) else None,
                )
                if _loc_reply and str(_loc_reply).strip():
                    _tid_loc = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="geo",
                        module="__fallback__",
                        fallback=False,
                        reason="telegram_location_direct",
                        skill_name="",
                        trace_id=str(_tid_loc),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="geo", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "telegram_location",
                                    "direct_reply": str(_loc_reply).strip(),
                                },
                            )
                        ],
                        mode="full",
                    )
            except Exception as e:
                logger.debug("telegram_location plan: %s", e)
        if user_id and text_stripped:
            try:
                from core.brain_own_turn import planner_direct_allowed

                if not planner_direct_allowed("geo_nearby"):
                    raise RuntimeError("brain_owns_geo_nearby")
                from core.geo_nearby_reply import try_geo_nearby_reply_sync

                _geo_reply = try_geo_nearby_reply_sync(
                    text_stripped,
                    meta=input_meta if isinstance(input_meta, dict) else None,
                    persisted=_persisted_plan if user_id else persisted,
                )
                if _geo_reply and str(_geo_reply).strip():
                    _tid_g = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="geo",
                        module="__fallback__",
                        fallback=False,
                        reason="geo_nearby_direct",
                        skill_name="",
                        trace_id=str(_tid_g),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="geo", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "geo_nearby",
                                    "direct_reply": str(_geo_reply).strip(),
                                },
                            )
                        ],
                        mode="full",
                    )
            except RuntimeError:
                from core.brain_own_turn import record_planner_semantic_deferred

                record_planner_semantic_deferred("geo_nearby")
            except Exception as e:
                logger.debug("geo_nearby plan: %s", e)
        if user_id and text_stripped:
            try:
                from core.brain_own_turn import planner_direct_allowed

                if not planner_direct_allowed("weather"):
                    raise RuntimeError("brain_owns_weather")
                _persisted_wx = _persisted_plan if user_id else persisted
                _recent_wx = (
                    _persisted_wx.get("recent_messages")
                    if isinstance(_persisted_wx, dict)
                    else None
                )
                try:
                    from core.intent_heuristics import detect_pre_llm_shortcut

                    _pre_lane = detect_pre_llm_shortcut(
                        text_stripped,
                        recent_dialogue=_recent_wx,
                        persisted=_persisted_wx if isinstance(_persisted_wx, dict) else None,
                    )
                    if _pre_lane == "weather_followup" and isinstance(input_meta, dict):
                        input_meta["pre_llm_lane"] = _pre_lane
                except Exception as e:
                    logger.debug("pre_llm_lane weather: %s", e)
                from core.turn_context import prepare_persisted_for_weather

                _facts_wx = (
                    _persisted_wx.get("user_facts")
                    if isinstance(_persisted_wx.get("user_facts"), dict)
                    else {}
                )
                prepare_persisted_for_weather(
                    _persisted_wx if isinstance(_persisted_wx, dict) else None,
                    _facts_wx,
                    user_id=str(user_id),
                    group_id=group_id,
                )
                from core.weather_reply import try_weather_reply_sync

                _wx_reply = try_weather_reply_sync(
                    text_stripped,
                    persisted=_persisted_wx,
                    user_id=str(user_id),
                    group_id=group_id,
                )
                if _wx_reply and str(_wx_reply).strip():
                    _tid_w = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="weather",
                        module="__fallback__",
                        fallback=False,
                        reason="weather_direct",
                        skill_name="",
                        trace_id=str(_tid_w),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="weather", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "weather_direct",
                                    "direct_reply": str(_wx_reply).strip(),
                                },
                            )
                        ],
                        mode="full",
                    )
            except RuntimeError:
                from core.brain_own_turn import record_planner_semantic_deferred

                record_planner_semantic_deferred("weather")
            except Exception as e:
                logger.debug("weather_direct plan: %s", e)
        if user_id and text_stripped:
            try:
                _rd_math = _persisted_plan if user_id else persisted
                _recent_math = (
                    _rd_math.get("recent_messages") if isinstance(_rd_math, dict) else None
                )
                from core.referential_math_reply import try_referential_math_reply_sync

                _math_ref = try_referential_math_reply_sync(
                    text_stripped,
                    recent_dialogue=_recent_math,
                )
                if _math_ref and str(_math_ref).strip():
                    _tid_m = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="math",
                        module="__fallback__",
                        fallback=False,
                        reason="referential_math",
                        skill_name="",
                        trace_id=str(_tid_m),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="math", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "referential_math",
                                    "direct_reply": str(_math_ref).strip(),
                                },
                            )
                        ],
                        mode="full",
                    )
            except Exception as e:
                logger.debug("referential_math plan: %s", e)
        if user_id and text_stripped:
            try:
                from core.brain_own_turn import planner_direct_allowed

                if not planner_direct_allowed("affirmative_search"):
                    raise RuntimeError("brain_owns_affirmative_search")
                from core.news_reply import try_affirmative_search_reply_sync
                from core.user_facts import has_pending_facts_confirmation

                _rd = _persisted_plan if user_id else persisted
                if has_pending_facts_confirmation(_rd):
                    raise RuntimeError("facts_confirm_pending")
                _recent_plan = (
                    _rd.get("recent_messages") if isinstance(_rd, dict) else None
                )
                _aff_search = try_affirmative_search_reply_sync(
                    text_stripped,
                    persisted=_rd,
                    user_id=str(user_id),
                    recent_dialogue=_recent_plan,
                )
                if _aff_search and str(_aff_search).strip():
                    _tid_as = (
                        input_meta.get("trace_id") if isinstance(input_meta, dict) else None
                    ) or ""
                    record_planner_pulse(
                        intent="search",
                        module="__fallback__",
                        fallback=False,
                        reason="affirmative_search",
                        skill_name="",
                        trace_id=str(_tid_as),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="search", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "affirmative_search",
                                    "direct_reply": str(_aff_search).strip(),
                                },
                            )
                        ],
                        mode="full",
                    )
            except RuntimeError as e:
                if str(e) != "facts_confirm_pending":
                    from core.brain_own_turn import record_planner_semantic_deferred

                    record_planner_semantic_deferred("affirmative_search")
            except Exception as e:
                logger.debug("affirmative_search plan: %s", e)
        if user_id and text_stripped:
            try:
                from core.brain_own_turn import planner_direct_allowed

                if not planner_direct_allowed("news_item"):
                    raise RuntimeError("brain_owns_news_item")
                from core.news_reply import try_news_item_reply_pack_sync

                _rd = _persisted_plan if user_id else persisted
                _recent_plan = (
                    _rd.get("recent_messages") if isinstance(_rd, dict) else None
                )
                _news_item_pack = try_news_item_reply_pack_sync(
                    text_stripped,
                    persisted=_rd,
                    user_id=str(user_id),
                    recent_dialogue=_recent_plan,
                )
                _news_item_reply = (
                    str(_news_item_pack.get("text") or "").strip()
                    if isinstance(_news_item_pack, dict)
                    else ""
                )
                if _news_item_reply:
                    _tid_ni = (
                        input_meta.get("trace_id") if isinstance(input_meta, dict) else None
                    ) or ""
                    record_planner_pulse(
                        intent="news",
                        module="__fallback__",
                        fallback=False,
                        reason="news_item_direct",
                        skill_name="",
                        trace_id=str(_tid_ni),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="news", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "news_item_direct",
                                    "direct_reply": str(_news_item_reply).strip(),
                                    "news_item_pack": _news_item_pack
                                    if isinstance(_news_item_pack, dict)
                                    else None,
                                },
                            )
                        ],
                        mode="full",
                    )
            except RuntimeError:
                from core.brain_own_turn import record_planner_semantic_deferred

                record_planner_semantic_deferred("news_item")
            except Exception as e:
                logger.debug("news_item_direct plan: %s", e)
        if user_id and text_stripped:
            try:
                from core.brain_own_turn import news_digest_search_only_enabled, planner_direct_allowed

                if not planner_direct_allowed("news") and not news_digest_search_only_enabled():
                    raise RuntimeError("brain_owns_news")
                _rd = _persisted_plan if user_id else persisted
                _recent_nd = (
                    _rd.get("recent_messages") if isinstance(_rd, dict) else None
                )
                try:
                    from core.article_thread_followup import article_followup_blocks_news_digest

                    if article_followup_blocks_news_digest(
                        text_stripped,
                        _recent_nd,
                        _rd if isinstance(_rd, dict) else None,
                    ):
                        raise RuntimeError("article_thread_blocks_news")
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.debug("news_direct article block check: %s", e)
                from core.news_reply import try_news_reply_sync

                _news_reply = try_news_reply_sync(
                    text_stripped,
                    persisted=_rd,
                    user_id=str(user_id),
                    recent_dialogue=_recent_nd,
                )
                if _news_reply and str(_news_reply).strip():
                    _tid_n = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
                    record_planner_pulse(
                        intent="news",
                        module="__fallback__",
                        fallback=False,
                        reason="news_direct",
                        skill_name="",
                        trace_id=str(_tid_n),
                        maintenance_ran=False,
                        safe_mode=bool(
                            self._resilience.is_enabled() and self._resilience.is_safe_mode()
                        ),
                    )
                    record_usage(text=text_stripped, intent="news", module="__fallback__")
                    MONITOR.inc("planner_decisions_total")
                    _obs_mark(input_meta, "plan_done")
                    if self.behavior_store and isinstance(_rd, dict):
                        try:
                            self.behavior_store.save(user_id, group_id, _rd)
                        except Exception as e:
                            logger.debug("news_direct stash save: %s", e)
                    _ds_news = (
                        _rd.get("dialogue_state")
                        if isinstance(_rd.get("dialogue_state"), dict)
                        else {}
                    )
                    return Plan(
                        steps=[
                            PlanStep(
                                module_name="__fallback__",
                                args={
                                    "input": normalized_input,
                                    "context": {"user_id": user_id, "group_id": group_id},
                                    "fallback_variant": "news_direct",
                                    "direct_reply": str(_news_reply).strip(),
                                    "news_digest_context": {
                                        "items": _ds_news.get("last_news_digest_items"),
                                        "meta": _ds_news.get("last_news_digest_meta"),
                                    },
                                },
                            )
                        ],
                        mode="full",
                    )
            except RuntimeError as _news_skip:
                _skip_reason = str(_news_skip)
                if _skip_reason not in ("brain_owns_news", "article_thread_blocks_news"):
                    raise
                if _skip_reason == "article_thread_blocks_news":
                    logger.debug("news_direct skipped: article_thread context")
                elif _skip_reason == "brain_owns_news":
                    _rd_web = _persisted_plan if user_id else persisted
                    _recent_web = (
                        _rd_web.get("recent_messages") if isinstance(_rd_web, dict) else None
                    )
                    try:
                        from core.news_reply import try_web_news_digest_reply_sync

                        _web_news = try_web_news_digest_reply_sync(
                            text_stripped,
                            persisted=_rd_web,
                            user_id=str(user_id),
                            recent_dialogue=_recent_web,
                        )
                        if _web_news and str(_web_news).strip():
                            _tid_wn = (
                                input_meta.get("trace_id") if isinstance(input_meta, dict) else None
                            ) or ""
                            record_planner_pulse(
                                intent="news",
                                module="__fallback__",
                                fallback=False,
                                reason="news_web_search",
                                skill_name="",
                                trace_id=str(_tid_wn),
                                maintenance_ran=False,
                                safe_mode=bool(
                                    self._resilience.is_enabled() and self._resilience.is_safe_mode()
                                ),
                            )
                            record_usage(text=text_stripped, intent="news", module="__fallback__")
                            MONITOR.inc("planner_decisions_total")
                            _obs_mark(input_meta, "plan_done")
                            return Plan(
                                steps=[
                                    PlanStep(
                                        module_name="__fallback__",
                                        args={
                                            "input": normalized_input,
                                            "context": {"user_id": user_id, "group_id": group_id},
                                            "fallback_variant": "news_web_search",
                                            "direct_reply": str(_web_news).strip(),
                                        },
                                    )
                                ],
                                mode="full",
                            )
                    except Exception as e:
                        logger.debug("news_web_search plan: %s", e)
                    from core.brain_own_turn import record_planner_semantic_deferred

                    record_planner_semantic_deferred("news")
            except Exception as e:
                logger.debug("news_direct plan: %s", e)

        if user_id and isinstance(_tl_plan, dict) and self.behavior_store:
            try:
                from core.user_issue_journal import persist_last_location

                persisted = persist_last_location(persisted, _tl_plan)
                try:
                    from core.weather_location_store import (
                        anchor_from_telegram,
                        apply_weather_anchor,
                    )

                    _wa = anchor_from_telegram(_tl_plan)
                    if _wa:
                        apply_weather_anchor(
                            self.behavior_store, str(user_id), group_id, _wa
                        )
                        persisted["weather_anchor"] = _wa
                except Exception as e:
                    logger.debug("weather_anchor from telegram: %s", e)
                self.behavior_store.save(user_id, group_id, persisted)
            except Exception as e:
                logger.debug("persist_last_location: %s", e)
        try:
            from core.agent_kv.grim import hydrate_cdc_from_kv
            from core.agent_kv.store import agent_kv_enabled

            if user_id and agent_kv_enabled():
                persisted = hydrate_cdc_from_kv(str(user_id), persisted)
        except Exception as e:
            logger.debug("hydrate_cdc_from_kv: %s", e)
        try:
            from core.affect_state import hydrate_affect_from_kv

            if user_id:
                persisted = hydrate_affect_from_kv(str(user_id), persisted)
        except Exception as e:
            logger.debug("hydrate_affect_from_kv: %s", e)
        try:
            if user_id:
                persisted = hydrate_self_model_from_kv(str(user_id), persisted)
        except Exception as e:
            logger.debug("hydrate_self_model_from_kv: %s", e)
        # Авто-заполнение timezone из страны/города если не указан явно.
        # Мутирует persisted["user_facts"] — при сохранении в конце хода
        # timezone запишется на диск и переживёт перезагрузку.
        try:
            from core.timezone_inference import (
                apply_stated_timezone_to_facts,
                ensure_timezone_in_user_facts,
            )

            _uf_tz = persisted.get("user_facts")
            if isinstance(_uf_tz, dict):
                if text:
                    apply_stated_timezone_to_facts(text, _uf_tz)
                ensure_timezone_in_user_facts(_uf_tz)
        except Exception as e:
            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        pre_ctx = self._assemble_brain_context(user_id, group_id, persisted=persisted)
        # Batch continuation hint: если есть неотвеченные пункты и запрос на продолжение
        try:
            from core.batch_continuation import get_pending, is_continuation, build_continuation_hint

            if text and is_continuation(text) and get_pending(persisted):
                _batch_hint = build_continuation_hint(persisted, text)
                if _batch_hint:
                    existing = str(pre_ctx.get("routing_prefs_hint") or "")
                    if existing:
                        pre_ctx["routing_prefs_hint"] = f"{existing}\n\n{_batch_hint}"
                    else:
                        pre_ctx["routing_prefs_hint"] = _batch_hint
                    logger.info("[batch] continuation hint injected: %d chars", len(_batch_hint))
                # Force batch profile on continuation turn — prevents bypass to "short"
                pre_ctx["brain_force_batch_profile"] = True
        except Exception as e:
            logger.debug("batch_continuation hint: %s", e)
        try:
            from core.turn_reconcile import apply_discourse_and_collapse_sync

            text, pre_ctx, _slots_mutated = apply_discourse_and_collapse_sync(
                text,
                pre_ctx,
                persisted=persisted if isinstance(persisted, dict) else None,
            )
            if _slots_mutated and user_id and isinstance(persisted, dict):
                self.behavior_store.save(user_id, group_id, persisted)
        except Exception as e:
            logger.debug("turn_reconcile plan: %s", e)
        _obs_mark(input_meta, "plan_context")
        maintenance = self._maintenance.maybe_run(interval_sec=self._maintenance_interval_sec)
        if maintenance.get("ran"):
            MONITOR.inc("maintenance_cycles_total")
            try:
                hr = get_host_resource_snapshot(force=True)
                self._host_adaptation_hints = list(hr.get("adaptation_hints") or [])
            except Exception as e:
                logger.debug("host_resources refresh: %s", e)
            try:
                self._recovery_autonomy.tick(self, maintenance_ran=True)
            except Exception as e:
                logger.debug("recovery_autonomy tick: %s", e)
            try:
                self._resilience.tick(self, maintenance_ran=True)
            except Exception as e:
                logger.debug("resilience tick: %s", e)
            # Autonomy 3.0: self-optimization analysis (second maintenance point)
            try:
                _opt2 = self_healing_analyze()
                if _opt2.get("suggestions"):
                    logger.info("self_optimization(2): %s", _opt2)
            except Exception as e:
                logger.debug("self_optimization(2): %s", e)
        _obs_mark(input_meta, "plan_maintenance")
        ingested_rows = self._knowledge_engine.ingest_context_sources(context=pre_ctx)
        MONITOR.inc("knowledge_rows_ingested_total", max(0, int(ingested_rows)))
        intent_probe = (
            self._detect_intent(text, persisted, file_context=file_context, planner_context=pre_ctx)
            if text
            else "empty"
        )
        planner_knowledge_hint = self._knowledge_engine.select_for_intent(intent_probe)
        predictive_hint = self._predictive.predict(
            text=text,
            recent_dialogue=pre_ctx.get("recent_dialogue") or [],
            topic_tracking=pre_ctx.get("topic_tracking") or {},
            psychology=pre_ctx.get("psychology") or {},
            user_facts=pre_ctx.get("user_facts") or {},
        )
        goal_state = self._goal_engine.load_state(persisted)
        goal_hints = self._goal_engine.planning_hints(goal_state)
        if predictive_hint.get("confidence", 0.0) >= self._predictive_conf_threshold:
            MONITOR.inc("predictive_confident_total")
        if planner_knowledge_hint.get("policy") == "fresh_trusted_tagged":
            MONITOR.inc("knowledge_hint_policy_fresh_total")

        allowed = self._allowed_module_keys(user_id, group_id)
        decision = self._planner.decide(
            text=text,
            allowed_modules=allowed,
            route_command=self._route_command,
            detect_intent=lambda raw, p=persisted, fc=file_context, pc=pre_ctx: self._detect_intent(
                raw, p, file_context=fc, planner_context=pc
            ),
            select_module=self._select_module,
            input_meta=input_meta if isinstance(input_meta, dict) else {},
            knowledge_hint=planner_knowledge_hint,
        )
        if user_id:
            try:
                from core.cdc import cdc_enabled, maybe_apply_planner_penalty

                if cdc_enabled():
                    decision = maybe_apply_planner_penalty(decision, persisted, allowed)
            except Exception as e:
                logger.debug("cdc planner penalty: %s", e)
        cost_patch: Dict[str, Any] = {}
        if cost_autopilot_enabled():
            try:
                cost_patch = build_cost_autopilot_patch(
                    user_text=text,
                    planned_intent=decision.intent,
                    planned_module=decision.module_name,
                    predictive_hint=predictive_hint if isinstance(predictive_hint, dict) else {},
                    has_rich_context=bool(has_rich),
                )
            except Exception as e:
                logger.debug("cost autopilot patch: %s", e)
        efficiency_patch: Dict[str, Any] = {}
        if efficiency_guard_enabled():
            try:
                efficiency_patch = build_efficiency_guard_patch(orchestrator=self, days=7.0)
            except Exception as e:
                logger.debug("efficiency guard patch: %s", e)
        _obs_mark(input_meta, "plan_decide")
        MONITOR.inc("planner_decisions_total")
        if gemma_core_log_full():
            tid = (input_meta.get("trace_id") if isinstance(input_meta, dict) else None) or ""
            logger.info(
                "[CORE] planner intent=%s module=%s fallback=%s reason=%s skill=%s trace=%s maintenance_ran=%s safe_mode=%s",
                decision.intent,
                decision.module_name,
                decision.fallback,
                decision.reason,
                decision.skill_name or "-",
                tid[:12] if tid else "-",
                bool(maintenance.get("ran")),
                bool(self._resilience.is_enabled() and self._resilience.is_safe_mode()),
                extra={
                    "gemma_event": "planner_decision",
                    "trace_id": tid or None,
                    "intent": decision.intent,
                    "gemma_module": decision.module_name,
                    "fallback": decision.fallback,
                    "reason": decision.reason,
                    "skill_name": decision.skill_name or None,
                },
            )
        record_planner_pulse(
            intent=decision.intent,
            module=decision.module_name,
            fallback=decision.fallback,
            reason=decision.reason,
            skill_name=decision.skill_name or "",
            trace_id=str((input_meta.get("trace_id") if isinstance(input_meta, dict) else "") or ""),
            maintenance_ran=bool(maintenance.get("ran")),
            safe_mode=bool(self._resilience.is_enabled() and self._resilience.is_safe_mode()),
        )
        record_usage(text=text, intent=decision.intent, module=decision.module_name)
        if decision.fallback:
            MONITOR.inc("planner_fallback_total")
        module_key = decision.module_name
        planned_intent = decision.intent
        steps: List[PlanStep] = []

        facts_flow: Dict[str, Any] = {}
        scenario_forecast = None
        if user_id:
            try:
                # Facts extraction/confirmation is context-only and does not affect routing.
                facts_flow = self.user_facts_manager.process_turn(user_id, group_id, text)
            except Exception as e:
                logger.debug("user facts processing failed: %s", e)
                facts_flow = {}
            try:
                from core.scenario_engine import (
                    TurnContext,
                    apply_forecast_to_facts_flow,
                    forecast_pre_turn,
                )

                _chat_id = str((input_meta.get("chat_id") if isinstance(input_meta, dict) else "") or user_id or "")
                _pending_bug = False
                try:
                    from core.user_bug_report import has_pending

                    _pending_bug = has_pending(str(user_id), _chat_id)
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                scenario_forecast = forecast_pre_turn(
                    TurnContext(
                        user_text=text,
                        user_id=str(user_id),
                        chat_id=_chat_id,
                        group_id=str(group_id) if group_id else None,
                        intent=planned_intent,
                        module=module_key,
                        has_attachment=bool(file_context),
                        file_type=str((file_context or {}).get("file_type") or ""),
                        facts_flow=facts_flow if isinstance(facts_flow, dict) else {},
                        dialogue_state=(persisted or {}).get("dialogue_state")
                        if isinstance(persisted, dict)
                        else {},
                        pending_bug_report=_pending_bug,
                    )
                )
                facts_flow = apply_forecast_to_facts_flow(
                    facts_flow if isinstance(facts_flow, dict) else {}, scenario_forecast
                )
                if isinstance(input_meta, dict) and scenario_forecast.hits:
                    input_meta["scenario_forecast"] = scenario_forecast.to_dict()
                    MONITOR.inc("scenario_forecast_hits_total", len(scenario_forecast.hits))
            except Exception as e:
                logger.debug("scenario_engine pre_plan: %s", e)
            if facts_flow:
                persisted = self.behavior_store.load(user_id, group_id)
                pre_ctx = self._assemble_brain_context(user_id, group_id, persisted=persisted)
                ingested_rows = self._knowledge_engine.ingest_context_sources(context=pre_ctx)
                MONITOR.inc("knowledge_rows_ingested_total", max(0, int(ingested_rows)))
                goal_state = self._goal_engine.load_state(persisted)
                goal_hints = self._goal_engine.planning_hints(goal_state)

        use_math_ambiguous_fallback = False
        if (
            _env_truthy("MATH_AMBIGUOUS_CLARIFY", True)
            and text_stripped
            and planned_intent == "general"
            and module_key in _DIALOG_PLAN_MODULES
            and not decision.fallback
        ):
            rp0 = (persisted or {}).get("routing_prefs") or {}
            if not (isinstance(rp0, dict) and rp0.get("prefer_general_over_math")):
                try:
                    from core.intent_heuristics import math_route_is_ambiguous

                    use_math_ambiguous_fallback = math_route_is_ambiguous(text_stripped)
                except Exception as e:
                    logger.debug("math_route_is_ambiguous: %s", e)

        _scenario_kw = (
            {"scenario_forecast": scenario_forecast} if scenario_forecast is not None else {}
        )
        lookahead_plan: Optional[Dict[str, Any]] = None
        try:
            from core.lookahead_planner import build_lookahead_plan as _build_lookahead_plan
            from core.lookahead_planner import enabled as _lookahead_enabled

            if _lookahead_enabled():
                lookahead_plan = _build_lookahead_plan(
                    user_text=text,
                    intent=planned_intent,
                    module=module_key,
                    planner_reason=str(decision.reason or ""),
                    fallback=bool(decision.fallback),
                    goal_hints=goal_hints if isinstance(goal_hints, dict) else {},
                    predictive_hint=predictive_hint if isinstance(predictive_hint, dict) else {},
                    knowledge_hint=planner_knowledge_hint if isinstance(planner_knowledge_hint, dict) else {},
                    skill_name=str(decision.skill_name or ""),
                )
                if lookahead_plan:
                    MONITOR.inc("lookahead_plan_built_total")
        except Exception as e:
            logger.debug("lookahead_planner: %s", e)
            lookahead_plan = None
        try:
            bus.emit_ff(
                "planner.decision",
                {
                    "user_id": str(user_id or ""),
                    "intent": decision.intent,
                    "module": decision.module_name,
                    "fallback": bool(decision.fallback),
                    "reason": str(decision.reason or ""),
                },
            )
        except Exception as e:
            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        if use_math_ambiguous_fallback:
            logger.info("[PLAN] math ambiguous -> short clarify fallback")
            step_knowledge_hint = self._knowledge_engine.select_for_intent(planned_intent)
            ctx = self._build_step_context(
                user_id=user_id,
                group_id=group_id,
                normalized_input=normalized_input,
                persisted=persisted,
                decision=decision,
                planned_module="__fallback__",
                planned_intent=planned_intent,
                text=text,
                file_context=file_context,
                doc_context=doc_context,
                code_context=code_context,
                facts_flow=facts_flow,
                knowledge_hint=step_knowledge_hint,
                predictive_hint=predictive_hint,
                goal_hints=goal_hints,
                cached_brain_context=pre_ctx,
                maintenance_ran=bool(maintenance.get("ran")),
                lookahead_plan=lookahead_plan,
                cost_patch=cost_patch,
                efficiency_patch=efficiency_patch,
                **_scenario_kw,
            )
            steps.append(
                PlanStep(
                    module_name="__fallback__",
                    args={
                        "input": normalized_input,
                        "context": ctx,
                        "fallback_variant": "math_ambiguous",
                    },
                )
            )
        elif module_key and module_key != "__fallback__":
            step_knowledge_hint = self._knowledge_engine.select_for_intent(planned_intent)
            ctx = self._build_step_context(
                user_id=user_id,
                group_id=group_id,
                normalized_input=normalized_input,
                persisted=persisted,
                decision=decision,
                planned_module=module_key,
                planned_intent=planned_intent,
                text=text,
                file_context=file_context,
                doc_context=doc_context,
                code_context=code_context,
                facts_flow=facts_flow,
                knowledge_hint=step_knowledge_hint,
                predictive_hint=predictive_hint,
                goal_hints=goal_hints,
                cached_brain_context=pre_ctx,
                maintenance_ran=bool(maintenance.get("ran")),
                lookahead_plan=lookahead_plan,
                cost_patch=cost_patch,
                efficiency_patch=efficiency_patch,
                **_scenario_kw,
            )
            steps.append(
                PlanStep(
                    module_name=module_key,
                    args={
                        "input": normalized_input,
                        "context": ctx,
                    },
                )
            )
        else:
            from core.unified_planner import pick_dialog_module

            _rescue_dm = (
                pick_dialog_module(allowed)
                if text_stripped
                and str(decision.reason or "") not in ("unknown_command",)
                else None
            )
            if _rescue_dm:
                logger.info("[PLAN] rescue dialog module=%s (was fallback)", _rescue_dm)
                module_key = _rescue_dm
                step_knowledge_hint = self._knowledge_engine.select_for_intent(planned_intent)
                ctx = self._build_step_context(
                    user_id=user_id,
                    group_id=group_id,
                    normalized_input=normalized_input,
                    persisted=persisted,
                    decision=decision,
                    planned_module=module_key,
                    planned_intent=planned_intent,
                    text=text,
                    file_context=file_context,
                    doc_context=doc_context,
                    code_context=code_context,
                    facts_flow=facts_flow,
                    knowledge_hint=step_knowledge_hint,
                    predictive_hint=predictive_hint,
                    goal_hints=goal_hints,
                    cached_brain_context=pre_ctx,
                    maintenance_ran=bool(maintenance.get("ran")),
                    lookahead_plan=lookahead_plan,
                    cost_patch=cost_patch,
                    efficiency_patch=efficiency_patch,
                    **_scenario_kw,
                )
                steps.append(
                    PlanStep(
                        module_name=module_key,
                        args={"input": normalized_input, "context": ctx},
                    )
                )
            else:
                logger.info("[PLAN] no module selected, using fallback")
                step_knowledge_hint = self._knowledge_engine.select_for_intent(planned_intent)
                ctx = self._build_step_context(
                    user_id=user_id,
                    group_id=group_id,
                    normalized_input=normalized_input,
                    persisted=persisted,
                    decision=decision,
                    planned_module="__fallback__",
                    planned_intent=planned_intent,
                    text=text,
                    file_context=file_context,
                    doc_context=doc_context,
                    code_context=code_context,
                    facts_flow=facts_flow,
                    knowledge_hint=step_knowledge_hint,
                    predictive_hint=predictive_hint,
                    goal_hints=goal_hints,
                    cached_brain_context=pre_ctx,
                    maintenance_ran=bool(maintenance.get("ran")),
                    lookahead_plan=lookahead_plan,
                    cost_patch=cost_patch,
                    efficiency_patch=efficiency_patch,
                    **_scenario_kw,
                )
                steps.append(
                    PlanStep(
                        module_name="__fallback__",
                        args={
                            "input": normalized_input,
                            "context": ctx,
                        },
                    )
                )

        if self._resilience.is_enabled() and self._resilience.is_safe_mode():
            _obs_mark(input_meta, "plan_done")
            return Plan(steps=steps, mode="degraded")
        _obs_mark(input_meta, "plan_done")
        return Plan(steps=steps, mode="full")

    def _route_command(self, text: str, allowed: set) -> Optional[str]:
        raw = text.split()[0]
        cmd = raw.lstrip("/").split("@")[0].lower()

        for module_key, module in self.plugin_registry.loaded_modules.items():
            if not self.plugin_controller.is_routable(module_key):
                continue
            if module_key not in allowed:
                continue
            manifest = getattr(module, "manifest", None)
            if not manifest:
                continue

            if hasattr(manifest, "iter_command_tokens"):
                tokens = manifest.iter_command_tokens()
            else:
                tokens = []

            for token in tokens:
                if token == cmd:
                    logger.info(f"[ROUTE] command=/{cmd} -> module={module_key}")
                    return module_key

        # Ни один плагин не зарегистрировал команду — проверяем каталог ядра.
        # Известные core-команды (weather, wiki, goal_*, calc, search и т.п.)
        # направляем в диалоговый модуль, чтобы LLM обработала их через инструменты.
        from core.command_catalog import find_core_spec, is_core_exclusive_token

        if is_core_exclusive_token(cmd):
            logger.info("[ROUTE] command=/%s -> core exclusive (skip plugin route)", cmd)
            return None

        if find_core_spec(cmd) is not None:
            for dk in ("chat-orchestrator", "chat_orchestrator", "smartchat"):
                if dk in allowed and dk in self.plugin_registry.loaded_modules:
                    logger.info(f"[ROUTE] command=/{cmd} -> {dk} (core fallback)")
                    return dk

        logger.info(f"[ROUTE] command=/{cmd} -> no module found")
        return None

    def _detect_intent(
        self,
        text: str,
        persisted: Optional[Dict[str, Any]] = None,
        *,
        file_context: Optional[Dict[str, Any]] = None,
        planner_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        «math» только если после удаления URL/инвайтов всё ещё похоже на выражение.
        Учитывает routing_prefs (пользователь просил не навязывать калькулятор).
        """
        from core.intent_heuristics import (
            explicit_math_request,
            naive_math_intent_from_text,
            strip_urls_and_mentions_for_math_probe,
        )

        raw = (text or "").strip()
        if not raw:
            return "empty"
        try:
            from core.intent_heuristics import is_system_operator_directive

            if is_system_operator_directive(raw):
                logger.info("[PLAN] intent -> general (system operator directive)")
                return "general"
        except Exception as e:
            logger.debug("system_operator_directive: %s", e)
        try:
            from core.spatial_design.route import wants_spatial_design_intent

            if wants_spatial_design_intent(raw, file_context=file_context, persisted=persisted):
                logger.info("[PLAN] intent -> spatial_design (plan feedback loop)")
                MONITOR.inc("spatial_design_intent_total")
                return "spatial_design"
        except Exception as e:
            logger.debug("spatial_design intent: %s", e)
        try:
            from core.image_gen_nl import (
                attachment_wants_image_generation,
                image_gen_nl_route_enabled,
                prose_wants_image_gen_or_edit,
            )

            if attachment_wants_image_generation(file_context, raw):
                logger.info("[PLAN] intent -> image_generation (user image + nl/edit)")
                MONITOR.inc("image_gen_nl_intent_total")
                return "image_generation"
            if image_gen_nl_route_enabled() and prose_wants_image_gen_or_edit(raw):
                logger.info("[PLAN] intent -> image_generation (nl prose)")
                MONITOR.inc("image_gen_nl_intent_total")
                return "image_generation"
        except Exception as e:
            logger.debug("image_gen nl intent: %s", e)
        try:
            from core.module_gen_intent import plugin_programming_prefers_general

            if plugin_programming_prefers_general(raw):
                logger.info("[PLAN] intent -> general (plugin programming guard)")
                return "general"
        except Exception as e:
            logger.debug("plugin_programming_prefers_general: %s", e)
        override_intent = _intent_override_from_text(raw)
        if override_intent:
            logger.info("[PLAN] intent -> %s (extended override)", override_intent)
            return override_intent
        locked_intent = _intent_mode_continuation_lock(raw, persisted)
        if locked_intent:
            logger.info("[PLAN] intent lock -> %s (anti-drift continuation)", locked_intent)
            return locked_intent
        try:
            from core.brain.discourse_resolver import (
                inherited_intent_from_context,
                structural_thread_continuation,
            )

            pctx: Dict[str, Any] = {}
            if isinstance(planner_context, dict):
                pctx.update(planner_context)
            if isinstance(persisted, dict):
                pctx.setdefault("dialogue_state", persisted.get("dialogue_state"))
                pctx.setdefault("recent_dialogue", persisted.get("recent_messages"))
            inherited = inherited_intent_from_context(pctx)
            if not inherited:
                inherit, _ = structural_thread_continuation(raw, pctx)
                if inherit and isinstance(pctx.get("dialogue_state"), dict):
                    inherited = str(pctx["dialogue_state"].get("last_intent") or "").strip().lower()
            if inherited and inherited not in {"", "empty", "unknown"}:
                logger.info("[PLAN] intent -> %s (discourse inherit)", inherited)
                return inherited
        except Exception as e:
            logger.debug("discourse intent inherit: %s", e)
        try:
            from core.math_investment import text_looks_like_investment_annuity
            from core.math_linear import text_looks_like_equation_solve

            if text_looks_like_investment_annuity(raw):
                logger.info("[PLAN] intent -> math (investment annuity)")
                return "math"
            if text_looks_like_equation_solve(raw):
                logger.info("[PLAN] intent -> math (symbolic equation)")
                return "math"
        except Exception as e:
            logger.debug("equation_solve intent: %s", e)
        scrubbed = strip_urls_and_mentions_for_math_probe(raw)
        if not naive_math_intent_from_text(raw):
            return "general"
        if explicit_math_request(raw, scrubbed):
            return "math"
        if prefer_general_over_math_from_file():
            logger.info("[PLAN] intent math -> general (operator_rules.json global)")
            return "general"
        if force_general_intent_by_operator_patterns(raw):
            logger.info("[PLAN] intent math -> general (operator_rules patterns)")
            return "general"
        if force_general_when_math_probe(raw):
            logger.info("[PLAN] intent math -> general (ephemeral_lessons)")
            return "general"
        rp = (persisted or {}).get("routing_prefs") or {}
        if isinstance(rp, dict) and rp.get("prefer_general_over_math"):
            logger.info("[PLAN] intent math -> general (routing_prefs + non-explicit math)")
            return "general"
        # naive_math сработал, но explicit_math_request нет — типичный ложный срабатыватель в длинных текстах
        logger.info("[PLAN] intent naive-math probe -> general (no explicit math cue)")
        return "general"

    def _select_module(self, intent: str, allowed: set) -> Optional[str]:
        """Выбор модуля по intent; для general сначала основной диалог (chat-orchestrator), не случайный smartchat."""
        loaded = {
            k: v
            for k, v in self.plugin_registry.loaded_modules.items()
            if self.plugin_controller.is_routable(k)
        }

        def _first_with_capability(keys: Tuple[str, ...]) -> Optional[str]:
            for module_key in keys:
                if module_key not in allowed or module_key not in loaded:
                    continue
                module = loaded[module_key]
                manifest = getattr(module, "manifest", None)
                if not manifest:
                    continue
                caps = getattr(manifest, "capabilities", []) or []
                if intent in caps:
                    return module_key
            return None

        def _first_loaded(keys: Tuple[str, ...]) -> Optional[str]:
            for module_key in keys:
                if module_key in allowed and module_key in loaded:
                    return module_key
            return None

        # Intent -> preferred modules map (even when capabilities are broader/different).
        # This avoids collapsing all non-math traffic into general->chat-orchestrator.
        intent_pref_by_name: Dict[str, Tuple[str, ...]] = {
            "dialog_recall": ("dialog_memory_recall",),
            # User-facing reasoning should default to regular conversational solver.
            # reasoning_hub is treated as internal deterministic toolbox by slash commands.
            "reasoning": ("chat-orchestrator", "smartchat", "reasoning_hub", "meta_reasoning_layer"),
            "logic": ("chat-orchestrator", "smartchat", "reasoning_hub", "meta_reasoning_layer"),
            # "test" в обычном чате чаще означает проверку рассуждения, а не вызов учебного модуля.
            # School assistant остаётся доступен по slash-командам (/solve, /check, /explain, /quiz).
            "test": ("chat-orchestrator", "school_assistant", "smartchat"),
            # В обычном чате explain-запросы (особенно длинные reasoning/4D/matrix) не должны
            # падать в school_assistant с шаблоном "Анализ задачи". Учебный модуль остаётся
            # доступным по slash-командам.
            "explain": ("chat-orchestrator", "school_assistant", "smartchat"),
            "teacher": ("chat-orchestrator", "school_assistant", "smartchat"),
            "code": ("chat-orchestrator", "smartchat"),
            "spatial_design": ("spatial_design",),
        }
        pref_names = intent_pref_by_name.get((intent or "").strip().lower())
        if pref_names:
            pref_loaded = _first_loaded(pref_names)
            if pref_loaded:
                logger.info(f"[SELECT] intent={intent} -> module={pref_loaded} (intent preferred map)")
                return pref_loaded

        if intent == "general":
            preferred = _first_with_capability(("chat-orchestrator", "smartchat"))
            if preferred:
                logger.info(f"[SELECT] intent={intent} -> module={preferred} (preferred dialog)")
                return preferred

        if intent == "image_generation":
            preferred = _first_with_capability(("image_generator",))
            if preferred:
                logger.info(f"[SELECT] intent={intent} -> module={preferred} (image generation)")
                return preferred

        if intent == "spatial_design":
            preferred = _first_with_capability(("spatial_design",))
            if preferred:
                logger.info(f"[SELECT] intent={intent} -> module={preferred} (spatial design)")
                return preferred

        for module_key, module in loaded.items():
            if module_key not in allowed:
                continue
            manifest = getattr(module, "manifest", None)
            if not manifest:
                continue
            caps = getattr(manifest, "capabilities", []) or []
            if intent in caps:
                logger.info(f"[SELECT] intent={intent} -> module={module_key}")
                return module_key
        logger.info(f"[SELECT] intent={intent} -> no module found")
        return None

    async def execute_plan(self, plan: Plan, user_id: str = None, group_id: str = None) -> List[Output]:
        outputs: List[Output] = []
        user_payload = ""
        _cdc_policy_patch: Dict[str, Any] = {}
        pre_ctx: Dict[str, Any] = {}
        trace_meta: Dict[str, Any] = {}
        inp0: Dict[str, Any] = {}
        _exec_start_ts = time.monotonic()
        if plan.steps:
            inp0 = (plan.steps[0].args or {}).get("input") or {}
            if not isinstance(inp0, dict):
                inp0 = {}
            trace_meta = inp0.get("meta") or {}
        _obs_mark(trace_meta, "exec_start")
        if plan.steps and user_id:
            _early_pl = str(inp0.get("payload", "") or "").strip()
            if _early_pl:
                # Goal Runner: только явная многошаговая цель с инструментами (не «нужно/хочу» substring).
                _is_goal = False
                try:
                    from core.brain.goal_runner_nudge import warrants_multistep_goal_text
                    from core.goal_runner_types import TaskType, classify_goal_runner_need

                    if warrants_multistep_goal_text(_early_pl):
                        _is_goal = classify_goal_runner_need(_early_pl) == TaskType.MULTISTEP_TOOL
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                if _is_goal:
                    try:
                        from core.goal_runner import try_goal_runner_turn

                        _gr = await try_goal_runner_turn(
                            orchestrator=self,
                            user_id=str(user_id),
                            group_id=group_id,
                            user_text=_early_pl,
                            source="executor",
                        )
                        if _gr is not None:
                            return _gr
                    except Exception as e:
                        logger.debug("goal_runner turn: %s", e)
        # meta_intent_probe disabled — reasoning pipeline handles routing
        if False and plan.steps and user_id:
            _up_meta = str(inp0.get("payload") or "").strip()
            if _up_meta:
                c_first = (plan.steps[0].args or {}).get("context")
                if isinstance(c_first, dict):
                    try:
                        from core.meta_intent_probe import apply_meta_intent_pack, compute_meta_intent_pack

                        _pack = await compute_meta_intent_pack(
                            ctx_template=c_first,
                            user_text=_up_meta,
                            input_obj=inp0,
                        )
                        _rp: Dict[str, Any] = {}
                        if self.behavior_store:
                            _pe = self.behavior_store.load(user_id, group_id)
                            if isinstance(_pe.get("routing_prefs"), dict):
                                _rp = _pe["routing_prefs"]
                        for _st in plan.steps:
                            _args = getattr(_st, "args", None) or {}
                            _cx = _args.get("context")
                            if isinstance(_cx, dict):
                                apply_meta_intent_pack(
                                    _cx,
                                    _pack,
                                    user_text=_up_meta,
                                    user_id=user_id,
                                    group_id=group_id,
                                    routing_prefs=_rp,
                                    input_obj=inp0,
                                )
                    except Exception as e:
                        logger.debug("meta_intent execute_plan: %s", e)
        # --- reasoning pipeline ---
        _skip_reasoning_nl = False
        if plan.steps:
            _a0 = getattr(plan.steps[0], "args", None) or {}
            if isinstance(_a0, dict) and _a0.get("fallback_variant") in _FALLBACK_DIRECT_REPLY_VARIANTS:
                _skip_reasoning_nl = True
        if plan.steps and user_id and not _skip_reasoning_nl:
            _up_reason = str(inp0.get("payload") or "").strip()
            if _up_reason:
                # Resolve named objects (e.g. "указ 95") before pronoun resolution
                _named = self.context_binder.resolve_object_by_name(_up_reason)
                _bound = self.context_binder.resolve_pronoun(_up_reason)
                _bound = _bound or _named

                # Subject context: subject pronouns (я, мне, меня, ...) override
                # any object/pronoun binding (priority: subject > explicit object > pronoun).
                _subject = self.context_binder.resolve_subject(_up_reason)
                if _subject is not None:
                    _bound = _subject

                # --- fast-path: intercept simple commands before full reasoning ---
                _fp_ctx = None
                if plan.steps:
                    _fp_ctx = (plan.steps[0].args or {}).get("context")
                _fp = fast_path(
                    _up_reason,
                    bound_object=_bound,
                    gate_context=_fp_ctx if isinstance(_fp_ctx, dict) else None,
                )
                if _fp is not None:
                    _is_tool = _fp["mode"] == "use_tool"

                    # ── Fast-Path Safety: tool-guard check ──
                    _fp_blocked = False
                    try:
                        from core.safety_config import fast_path_safety_enabled
                        if fast_path_safety_enabled() and _is_tool:
                            from core.tool_router import check_tool_call
                            _tgr = check_tool_call(
                                tool_name=_fp.get("tool", ""),
                                args=_fp.get("args"),
                                allow_self_programming=False,
                                is_fast_path=True,
                                has_explicit_tool_request=False,
                            )
                            if not _tgr.allowed:
                                _fp_blocked = True
                                _reasoning_state = {
                                    "mode": "just_answer",
                                    "intent": "direct_action",
                                    "topic": "",
                                    "should_call_tool": False,
                                    "reason": f"fast_path_tool_guard:{_tgr.reason}",
                                }
                                _reasoning_plan = ExecutionPlan(
                                    mode="just_answer",
                                    intent="direct_action",
                                    topic="",
                                    reason=f"fast_path_tool_guard:{_tgr.reason}",
                                )
                    except Exception as e:
                        logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                    if not _fp_blocked:
                        _reasoning_state = {
                            "mode": _fp["mode"],
                            "intent": "direct_tool_action" if _is_tool else "direct_action",
                            "topic": _fp.get("args", ""),
                            "should_call_tool": _is_tool,
                            "reason": "fast_path",
                        }
                        if _fp.get("tool"):
                            _reasoning_state["fast_path_tool"] = _fp["tool"]
                        if _fp.get("bound_object"):
                            _reasoning_state["bound_object"] = _fp["bound_object"]
                        _reasoning_plan = ExecutionPlan(
                            mode=_reasoning_state["mode"],
                            intent=_reasoning_state["intent"],
                            topic=_reasoning_state["topic"],
                            reason="fast_path",
                        )
                    _obs_mark(inp0.get("meta") if isinstance(inp0, dict) else {}, "reasoning_done")
                    for _st in plan.steps:
                        _args = getattr(_st, "args", None) or {}
                        _cx = _args.get("context")
                        if isinstance(_cx, dict):
                            _cx["reasoning_state"] = _reasoning_state
                            _cx["reasoning_plan"] = _reasoning_plan.to_dict()
                else:
                    try:
                        _c_first = (plan.steps[0].args or {}).get("context")

                        # Context binding: resolve pronominal / named references to object
                        _bound = self.context_binder.resolve_pronoun(_up_reason)
                        if _bound is not None and isinstance(_c_first, dict):
                            _c_first["bound_object"] = _bound.to_dict()

                        # ── Reasoning timer ──
                        try:
                            start_reasoning_timer()
                        except Exception as e:
                            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                        _reasoning_state = run_reasoning(
                            user_text=_up_reason,
                            bound_object=_bound,
                        )

                        # ── Reasoning time limit ──
                        try:
                            if reasoning_exceeded_time():
                                _reasoning_state = abort_reasoning()
                        except Exception as e:
                            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                        _reasoning_plan = build_plan(_reasoning_state)
                        _obs_mark(inp0.get("meta") if isinstance(inp0, dict) else {}, "reasoning_done")

                        # Store reasoning plan into every step's context
                        for _st in plan.steps:
                            _args = getattr(_st, "args", None) or {}
                            _cx = _args.get("context")
                            if isinstance(_cx, dict):
                                _cx["reasoning_state"] = _reasoning_state
                                _cx["reasoning_plan"] = _reasoning_plan.to_dict()
                    except Exception as e:
                        logger.debug("reasoning pipeline: %s", e)
        # --- direct_action override: force just_answer, skip all sub-systems ---
        for _st in plan.steps:
            _args = getattr(_st, "args", None) or {}
            _cx = _args.get("context")
            if isinstance(_cx, dict):
                _rs = _cx.get("reasoning_state")
                if isinstance(_rs, dict) and str(_rs.get("intent") or "") == "direct_action":
                    _rs["mode"] = "just_answer"
                    _rp = _cx.get("reasoning_plan")
                    if isinstance(_rp, dict):
                        _rp["mode"] = "just_answer"
                    logger.info(
                        "[DIRECT_ACTION] intent=direct_action mode=just_answer reason=%s skip=goal,tool,meta_intent,clarify,planning",
                        _rs.get("reason", ""),
                    )
        # ---------------------------------------------------------------------
        # Tool-chain 2-hop: if reasoning_plan reason == "tool_chain_2_hop",
        # insert additional PlanSteps for the second tool in the chain.
        _rp_c0 = (
            (plan.steps[0].args or {}).get("context")
            if plan.steps
            else None
        )
        if isinstance(_rp_c0, dict):
            _rp = _rp_c0.get("reasoning_plan")
            if isinstance(_rp, dict):
                _rs = _rp_c0.get("reasoning_state")
                _reason = str((_rp or {}).get("reason") or "")
                _canonical = str((_rs or {}).get("topic") or "")
            else:
                _reason = ""
                _canonical = ""
        else:
            _reason = ""
            _canonical = ""
        if _reason == "tool_chain_2_hop" and _canonical in TOOL_CHAINS:
            chain = TOOL_CHAINS[_canonical]
            if len(chain) >= 2:
                # Build a second PlanStep for the chained tool.
                # Clone the first step's args but set module_name to the second tool in chain.
                _chain_second = PlanStep(
                    module_name=chain[1],
                    args={
                        "input": (plan.steps[0].args or {}).get("input", {}),
                        "context": dict((plan.steps[0].args or {}).get("context") or {}),
                        "from_previous": True,
                    },
                )
                plan.steps.append(_chain_second)
        # ---------------------------------------------------------------------
        _prev_step_output = ""
        _exec_timeout_sec = float(os.getenv("EXECUTION_TIMEOUT_SEC", "20"))
        for _idx, step in enumerate(plan.steps):
            if time.monotonic() - _exec_start_ts > _exec_timeout_sec:
                logger.warning(
                    "[orchestrator] execution timeout (mid-step): %.1fs — aborting",
                    time.monotonic() - _exec_start_ts,
                )
                try:
                    reasoning_reset_chain("execution_timeout")
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                return [
                    Output(
                        type="text",
                        payload="Обработка заняла слишком много времени. Попробуй упростить запрос.",
                        meta={"module": "__fallback__", "reason": "execution_timeout"},
                    )
                ]
            args0 = getattr(step, "args", None) or {}
            # from_previous: carry previous step's textual output into current step args.
            if isinstance(args0, dict) and args0.get("from_previous") and _prev_step_output:
                args0 = dict(args0)
                args0["prev_output"] = _prev_step_output
                args0.pop("from_previous", None)
                setattr(step, "args", args0)
            inp0 = args0.get("input") or {}
            if isinstance(inp0, dict) and inp0.get("payload"):
                user_payload = str(inp0.get("payload", ""))
            c0 = args0.get("context")
            if isinstance(c0, dict):
                pre_ctx = c0
            res = await self._execute_step(step, user_id, group_id, step_index=_idx)
            _prev_step_output = " ".join(
                str(getattr(o, "payload", "") or "")
                for o in (res or [])
                if getattr(o, "type", None) == "text"
            ).strip()
            outputs.extend(res)
        _obs_mark(trace_meta, "exec_modules_done")

        # ── Telegram Timeout Protection ──
        _exec_elapsed = time.monotonic() - _exec_start_ts
        if _exec_elapsed > _exec_timeout_sec and not outputs:
            logger.warning(
                "[orchestrator] execution timeout: %.1fs — aborting", _exec_elapsed,
            )
            try:
                reasoning_reset_chain("execution_timeout")
            except Exception as e:
                logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
            return [
                Output(
                    type="text",
                    payload="Обработка заняла слишком много времени. Попробуй упростить запрос.",
                    meta={"module": "__fallback__", "reason": "execution_timeout"},
                )
            ]
        if _exec_elapsed > _exec_timeout_sec:
            logger.warning(
                "[orchestrator] execution timeout after reply: %.1fs — continue to turn.outcome",
                _exec_elapsed,
            )

        # ── LLM Latency Protection ──
        if _exec_elapsed > 10:
            try:
                reasoning_reset_chain("llm_latency")
            except Exception as e:
                logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        # ── exec_modules_done timeout interrupt ──
        _exec_modules_timeout_sec = float(os.getenv("EXEC_MODULES_TIMEOUT_SEC", "15"))
        if _exec_elapsed > _exec_modules_timeout_sec:
            logger.warning(
                "[orchestrator] exec_modules_done timeout: %.1fs", _exec_elapsed,
            )
            # Если ответ уже собран (brain/direct), всё равно пишем turn.outcome — иначе
            # turns.jsonl и C6 A/B пустые при типичном brain >15s.
            if not outputs:
                return [
                    Output(
                        type="text",
                        payload="Обработка прервана по таймауту.",
                        meta={"module": "__fallback__", "reason": "exec_modules_timeout"},
                    )
                ]
        # --- self-check ---
        if outputs and user_payload and os.getenv("SELF_VERIFY_ACTIVE", "false").lower() == "true":
            _rcx = (plan.steps[0].args or {}).get("context") if plan.steps else None
            _rs = _rcx.get("reasoning_state") if isinstance(_rcx, dict) else None
            if isinstance(_rs, dict) and str(_rs.get("mode") or "") == "just_answer":
                try:
                    from core.llm_tiered import llm_generate_tiered
                    from core.brain.runtime import _llm

                    _joined_check = " ".join(
                        str(getattr(o, "payload", "") or "")
                        for o in outputs or []
                        if getattr(o, "type", None) == "text"
                    ).strip()
                    if _joined_check:
                        _rs_intent = str(_rs.get("intent") or "")

                        async def _check_llm(*, prompt: str, system_prompt: str, max_tokens: int = 80, temperature: float = 0.0):
                            return await llm_generate_tiered(
                                _llm,
                                tag="self_check",
                                prompt=prompt,
                                system_prompt=system_prompt,
                                max_tokens=max_tokens,
                                temperature=temperature,
                            )

                        _rc_user_facts = _rcx.get("user_facts") if isinstance(_rcx, dict) else {}
                        _sv_name = str((_rc_user_facts or {}).get("name") or "").strip()
                        _fixed = await self_check_answer(
                            llm_call=_check_llm,
                            user_text=user_payload,
                            answer=_joined_check,
                            intent=_rs_intent,
                            user_name=_sv_name,
                        )
                        if _fixed != _joined_check:
                            for _o in outputs or []:
                                if getattr(_o, "type", None) == "text":
                                    _o.payload = _fixed
                                    break
                except Exception as e:
                    logger.debug("self_check answer: %s", e)
        # -------------------
        _ds_exec: Dict[str, Any] = {}
        _skill_name_exec = ""
        if isinstance(pre_ctx, dict):
            _raw_ds = pre_ctx.get("dialogue_state")
            if isinstance(_raw_ds, dict):
                _ds_exec = _raw_ds
            _kv_sd = pre_ctx.get("kv_session_debug")
            if isinstance(_kv_sd, dict):
                _ds_exec["kv_session_debug"] = _kv_sd
            _skill_name_exec = str(
                pre_ctx.get("_skill_name") or pre_ctx.get("planner_skill_name") or ""
            )
        _brain_tel = _brain_telemetry_from_plan(plan)
        if _brain_tel:
            if isinstance(_ds_exec, dict):
                if _brain_tel.get("prompt_tokens_est"):
                    _ds_exec["prompt_tokens_est"] = _brain_tel["prompt_tokens_est"]
                if _brain_tel.get("brain_recent_limit"):
                    _ds_exec["brain_recent_limit"] = _brain_tel["brain_recent_limit"]
                if _brain_tel.get("brain_profile") and not str(_ds_exec.get("brain_profile") or "").strip():
                    _ds_exec["brain_profile"] = _brain_tel["brain_profile"]
        if isinstance(_ds_exec, dict):
            _ds_exec["total_latency_ms"] = int(round(_exec_elapsed * 1000))
            if not str(_ds_exec.get("brain_profile") or "").strip():
                _bp = ""
                if isinstance(pre_ctx, dict):
                    _bp = str(
                        pre_ctx.get("brain_profile")
                        or pre_ctx.get("router_profile")
                        or ""
                    ).strip()
                if _bp:
                    _ds_exec["brain_profile"] = _bp
            if isinstance(pre_ctx, dict):
                _ra = pre_ctx.get("router_route_audit")
                if isinstance(_ra, dict) and _ra:
                    _ds_exec["router_route_audit"] = _ra
        if not _skill_name_exec and user_payload:
            try:
                from core.brain.translation_path import is_translation_turn
                from modules.skills.skill_router import resolve_skill_intent

                if is_translation_turn(user_payload):
                    _skill_name_exec = "translator"
                else:
                    _skill_name_exec = await resolve_skill_intent(user_payload) or ""
            except Exception as e:
                logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        try:
            from core.experience_memory import classify_turn_outcome, semantic_failure_reason

            outcome_all = classify_turn_outcome(outputs, user_text=user_payload)
        except Exception:
            outcome_all = "failure"
            semantic_failure = ""
            _joined_txt = ""
        else:
            semantic_failure = ""
            try:
                _joined_txt = " ".join(
                    str(getattr(o, "payload", "") or "")
                    for o in outputs or []
                    if getattr(o, "type", None) == "text"
                ).strip()
                semantic_failure = semantic_failure_reason(user_payload, _joined_txt) if _joined_txt else ""
            except Exception:
                semantic_failure = ""
            if semantic_failure:
                try:
                    for _o in outputs or []:
                        _meta = getattr(_o, "meta", None)
                        if isinstance(_meta, dict):
                            _meta["semantic_failure_reason"] = semantic_failure
                            break
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        try:
            _intent_norm = str(_ds_exec.get("last_intent") or "").strip().lower()
            if _intent_norm in {"reasoning", "logic"}:
                from core.answer_quality import has_concrete_answer, has_meta_tutor_text
                from core.reasoning_status import save_reasoning_quality_snapshot

                _final_answer_present = bool(has_concrete_answer(_joined_txt or ""))
                _no_meta_text = not bool(has_meta_tutor_text(_joined_txt or ""))
                _reasoning_completed = bool(_joined_txt) and bool(_final_answer_present) and bool(_no_meta_text)
                save_reasoning_quality_snapshot(
                    {
                        "intent": _intent_norm,
                        "module": str(_ds_exec.get("planned_module") or ""),
                        "outcome": outcome_all,
                        "final_answer_present": _final_answer_present,
                        "reasoning_completed": _reasoning_completed,
                        "no_meta_text": _no_meta_text,
                        "assistant_excerpt": (_joined_txt or "")[:1200],
                    }
                )
        except Exception as e:
            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        if plan.steps and user_id and (user_payload or "").strip():
            _stumble_detail = ""
            try:
                from core.session_digest import digest_enabled, record_turn

                if digest_enabled():
                    record_turn(
                        user_id=user_id,
                        user_text=user_payload,
                        outcome=outcome_all,
                        intent=str(_ds_exec.get("last_intent") or ""),
                        module=str(_ds_exec.get("planned_module") or ""),
                    )
            except Exception as e:
                logger.debug("session_digest: %s", e)
            try:
                from core.dialogue_feedback_signals import user_feedback_likely
                from core.route_risk_memory import (
                    record_stumble,
                    route_risk_enabled,
                    should_record_stumble,
                    stumble_detail_from_outputs,
                )

                _feedback_negative_pre = user_feedback_likely(
                    str(pre_ctx.get("last_user_message") or user_payload or "")
                )
                if route_risk_enabled():
                    _stumble_detail = stumble_detail_from_outputs(outputs)
                    if not _stumble_detail and semantic_failure:
                        _stumble_detail = semantic_failure
                    if should_record_stumble(
                        outcome=outcome_all,
                        detail=_stumble_detail or semantic_failure or "",
                        user_feedback_negative=_feedback_negative_pre,
                    ):
                        record_stumble(
                            user_text=user_payload,
                            intent=str(_ds_exec.get("last_intent") or ""),
                            module=str(_ds_exec.get("planned_module") or ""),
                            outcome=outcome_all,
                            detail=_stumble_detail,
                            skill_name=_skill_name_exec,
                        )
            except Exception as e:
                logger.debug("route_risk record: %s", e)
            try:
                from core.cdc import cdc_enabled, process_turn_end
                from core.route_risk_memory import classify_error_type

                if cdc_enabled():
                    _cdc_policy_patch = process_turn_end(
                        user_id=str(user_id),
                        user_text=user_payload,
                        intent=str(_ds_exec.get("last_intent") or ""),
                        module=str(_ds_exec.get("planned_module") or ""),
                        outcome=outcome_all,
                        task_tier=str(_ds_exec.get("task_tier") or ""),
                        detail=_stumble_detail,
                        error_type=classify_error_type(
                            outcome=outcome_all,
                            detail=_stumble_detail,
                            module=str(_ds_exec.get("planned_module") or ""),
                        ),
                        skill_name=_skill_name_exec,
                    )
                    try:
                        bus.emit_ff(
                            "cdc.policy.updated",
                            {
                                "user_id": str(user_id),
                                "intent": str(_ds_exec.get("last_intent") or ""),
                                "module": str(_ds_exec.get("planned_module") or ""),
                                "outcome": outcome_all,
                                "error_type": classify_error_type(
                                    outcome=outcome_all,
                                    detail=_stumble_detail,
                                    module=str(_ds_exec.get("planned_module") or ""),
                                ),
                                "cdc_policy": _cdc_policy_patch if isinstance(_cdc_policy_patch, dict) else {},
                            },
                        )
                    except Exception as e:
                        logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
            except Exception as e:
                logger.debug("cdc process_turn: %s", e)
            try:
                # Profile feedback: детекция фидбека в последнем сообщении пользователя
                _feedback_negative = False
                _feedback_positive = False
                try:
                    from core.dialogue_feedback_signals import user_feedback_likely
                    last_user_msg = str(pre_ctx.get("last_user_message") or "")
                    if user_feedback_likely(last_user_msg):
                        _feedback_negative = True
                    # Позитивный фидбек
                    low = last_user_msg.strip().lower()
                    if low in ("спасибо", "ok", "хорошо", "👍", "yes", "thanks", "thank you", "great", "отлично", "всё верно", "правильно"):
                        _feedback_positive = True
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                _comb_turn = " ".join(
                    str(getattr(o, "payload", "") or "") for o in outputs if getattr(o, "type", None) == "text"
                ).strip()
                _delivery_ns = ""
                _delivery_sk = ""
                for _o_del in outputs or []:
                    _m_del = getattr(_o_del, "meta", None)
                    if not isinstance(_m_del, dict):
                        continue
                    if _m_del.get("delivery_normalize_status"):
                        _delivery_ns = str(_m_del.get("delivery_normalize_status") or "")
                        _delivery_sk = str(_m_del.get("short_turn_kind") or "")
                        break
                try:
                    from core.brain.user_facing_contract import delivery_detail_suffix

                    _del_suf = delivery_detail_suffix(
                        normalize_status=_delivery_ns,
                        short_turn_kind=_delivery_sk,
                    )
                    if _del_suf:
                        _stumble_detail = (
                            f"{_stumble_detail} {_del_suf}".strip()
                            if _stumble_detail
                            else _del_suf
                        )
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                try:
                    from core.experience_memory import fingerprint as _fp_turn

                    _fp_turn_v = _fp_turn(user_payload or "")
                except Exception:
                    _fp_turn_v = ""
                _bt_pack = (
                    pre_ctx.get("brain_turn_telemetry")
                    if isinstance(pre_ctx, dict)
                    else None
                )
                def _coerce_int(val: Any) -> int:
                    try:
                        return int(val or 0)
                    except (TypeError, ValueError):
                        return 0

                _emit_prof = (
                    _brain_tel.get("brain_profile")
                    or (_bt_pack.get("brain_profile") if isinstance(_bt_pack, dict) else None)
                    or _ds_exec.get("brain_profile")
                    or _ds_exec.get("router_profile")
                    or (
                        str(pre_ctx.get("router_profile") or "").strip()
                        if isinstance(pre_ctx, dict)
                        else ""
                    )
                    or "standard"
                )
                _emit_pt = _coerce_int(
                    _brain_tel.get("prompt_tokens_est")
                    or (_bt_pack.get("prompt_tokens_est") if isinstance(_bt_pack, dict) else None)
                    or _ds_exec.get("prompt_tokens_est")
                )
                _emit_lim = _coerce_int(
                    _brain_tel.get("brain_recent_limit")
                    or (_bt_pack.get("brain_recent_limit") if isinstance(_bt_pack, dict) else None)
                    or _ds_exec.get("brain_recent_limit")
                )
                if _emit_lim <= 0:
                    from core.brain.brain_telemetry import brain_recent_limit_for_profile

                    _emit_lim = brain_recent_limit_for_profile(str(_emit_prof))
                _planner_bypass = None
                try:
                    if plan.steps and plan.steps[0].module_name == "__fallback__":
                        _a_pb = plan.steps[0].args if isinstance(plan.steps[0].args, dict) else {}
                        _planner_bypass = str(_a_pb.get("fallback_variant") or "").strip() or None
                except Exception as e:
                    logger.debug("planner_bypass extract: %s", e)
                _planner_reason_emit = ""
                if isinstance(_ds_exec, dict):
                    _planner_reason_emit = str(_ds_exec.get("planner_reason") or "").strip()
                _last_tool = ""
                _last_tool_ok = None
                try:
                    if self.behavior_store and user_id:
                        _st_rec = self.behavior_store.load(user_id, group_id)
                        _st_task = (
                            _st_rec.get("session_task")
                            if isinstance(_st_rec, dict)
                            else None
                        )
                        if isinstance(_st_task, dict):
                            _last_tool = str(_st_task.get("last_tool") or "").strip()
                            if "last_tool_ok" in _st_task:
                                _last_tool_ok = bool(_st_task.get("last_tool_ok"))
                except Exception as e:
                    logger.debug("last_tool extract: %s", e)
                _delivery_ok = bool(
                    outcome_all in ("ok", "success", "resolved")
                    and (_delivery_ns in ("", "ok") or not _delivery_ns)
                    and bool(_comb_turn)
                )
                _trace_id_emit = ""
                if isinstance(trace_meta, dict):
                    _trace_id_emit = str(trace_meta.get("trace_id") or "").strip()
                _stage_ms_emit = None
                _decision_trace_emit = None
                try:
                    from core.turn_telemetry import (
                        build_decision_trace,
                        stage_ms_for_trace_id,
                    )

                    _stage_ms_emit = stage_ms_for_trace_id(_trace_id_emit)
                    _ra_emit = (
                        _ds_exec.get("router_route_audit")
                        if isinstance(_ds_exec, dict)
                        else None
                    )
                    _decision_trace_emit = build_decision_trace(
                        planner_bypass=_planner_bypass,
                        planner_reason=_planner_reason_emit,
                        router_route_audit=_ra_emit,
                        profile=str(_emit_prof),
                        module=_emit_module_from_plan(
                            plan, _ds_exec if isinstance(_ds_exec, dict) else {}
                        ),
                        last_tool=_last_tool,
                        fallback_used=bool(_planner_bypass),
                    )
                except Exception as e:
                    logger.debug("turn_telemetry: %s", e)
                _art_thread_telem: Dict[str, Any] = {}
                if _planner_bypass == "article_thread_followup_nl":
                    try:
                        from core.article_thread_followup import extract_article_thread_subject

                        _a0 = (
                            plan.steps[0].args
                            if plan.steps and isinstance(plan.steps[0].args, dict)
                            else {}
                        )
                        _dr_head = str(_a0.get("direct_reply") or "").strip()[:80]
                        _rec_at = None
                        if self.behavior_store and user_id:
                            _rec_at = self.behavior_store.load(user_id, group_id)
                        _recent_at = (
                            _rec_at.get("recent_messages")
                            if isinstance(_rec_at, dict)
                            else None
                        )
                        _subj = extract_article_thread_subject(
                            _recent_at,
                            _rec_at if isinstance(_rec_at, dict) else None,
                        )
                        _art_thread_telem = {
                            "pre_llm_lane": "article_thread",
                            "direct_reply_head": _dr_head,
                            "article_thread_subject": str(_subj or "")[:120],
                        }
                    except Exception as e:
                        logger.debug("article_thread turn telemetry: %s", e)
                _turn_payload: Dict[str, Any] = {
                    "user_id": str(user_id),
                    "group_id": str(group_id) if group_id is not None else None,
                    "intent": str(_ds_exec.get("last_intent") or ""),
                    "profile": str(_emit_prof),
                    "module": _emit_module_from_plan(plan, _ds_exec if isinstance(_ds_exec, dict) else {}),
                    "planner_bypass": _planner_bypass,
                    "planner_reason": _planner_reason_emit or None,
                    "last_tool": _last_tool or None,
                    "last_tool_ok": _last_tool_ok,
                    "delivery_ok": _delivery_ok,
                    "skill": str(_skill_name_exec or "") or None,
                    "dialogue_lane": str(_ds_exec.get("dialogue_lane") or ""),
                    "outcome": outcome_all,
                    "detail": _stumble_detail,
                    "task_tier": str(_ds_exec.get("task_tier") or ""),
                    "latency_ms": _ds_exec.get("total_latency_ms", 0),
                    "prompt_tokens_est": _emit_pt,
                    "brain_recent_limit": _emit_lim,
                    "completion_tokens": _ds_exec.get("last_completion_tokens"),
                    "ok": outcome_all in ("ok", "success", "resolved"),
                    "user_feedback_negative": _feedback_negative,
                    "user_feedback_positive": _feedback_positive,
                    "user_excerpt": (user_payload or "")[:240],
                    "assistant_excerpt": _comb_turn[:480],
                    "fp": _fp_turn_v,
                    **_art_thread_telem,
                    "router_route_audit": (
                        _ds_exec.get("router_route_audit")
                        if isinstance(_ds_exec, dict)
                        else None
                    ),
                    "kv_session_debug": (
                        _ds_exec.get("kv_session_debug")
                        if isinstance(_ds_exec, dict)
                        else None
                    ),
                    "delivery_normalize_status": _delivery_ns or None,
                    "short_turn_kind": _delivery_sk or None,
                    "topic_tracking": (
                        pre_ctx.get("topic_tracking")
                        if isinstance(pre_ctx, dict)
                        and isinstance(pre_ctx.get("topic_tracking"), dict)
                        else None
                    ),
                    "stage_ms": _stage_ms_emit,
                    "decision_trace": _decision_trace_emit,
                    "trace_id": _trace_id_emit or None,
                    "compaction": (
                        _bt_pack.get("compaction")
                        if isinstance(_bt_pack, dict) and isinstance(_bt_pack.get("compaction"), dict)
                        else (
                            _ds_exec.get("compaction")
                            if isinstance(_ds_exec, dict) and isinstance(_ds_exec.get("compaction"), dict)
                            else None
                        )
                    ),
                }
                try:
                    from core.turn_reconcile import turn_state_audit_for_emit

                    _tsa_emit = turn_state_audit_for_emit(pre_ctx, plan)
                    if _tsa_emit:
                        _turn_payload["turn_state_audit"] = _tsa_emit
                    _da_emit = None
                    if isinstance(pre_ctx, dict) and isinstance(pre_ctx.get("discourse_audit"), dict):
                        _da_emit = pre_ctx.get("discourse_audit")
                    if _da_emit:
                        _turn_payload["discourse_audit"] = _da_emit
                except Exception as e:
                    logger.debug("turn_state_audit emit: %s", e)
                try:
                    from core.policy_memory_runtime import merge_memory_telemetry_into_turn_payload

                    _mem_tel = None
                    if isinstance(pre_ctx, dict):
                        _mem_tel = pre_ctx.get("memory_telemetry")
                    if not isinstance(_mem_tel, dict) and isinstance(_ds_exec, dict):
                        _mem_tel = _ds_exec.get("memory_telemetry")
                    merge_memory_telemetry_into_turn_payload(_turn_payload, _mem_tel)
                except Exception as e:
                    logger.debug("memory_telemetry turn: %s", e)
                bus.emit_ff("turn.outcome", _turn_payload)
            except Exception as e:
                logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
            try:
                from core.agent_kv.router_stats import record_router_turn
                from core.agent_kv.store import agent_kv_enabled

                if agent_kv_enabled():
                    record_router_turn(
                        user_id=str(user_id),
                        intent=str(_ds_exec.get("last_intent") or ""),
                        module=str(_ds_exec.get("planned_module") or ""),
                        outcome=outcome_all,
                        task_tier=str(_ds_exec.get("task_tier") or ""),
                    )
            except Exception as e:
                logger.debug("router_stats kv: %s", e)
            try:
                from core.affect_state import update_affect_after_turn
                from core.route_risk_memory import classify_error_type

                _aff = update_affect_after_turn(
                    user_id=str(user_id),
                    outcome=outcome_all,
                    task_tier=str(_ds_exec.get("task_tier") or ""),
                    error_type=classify_error_type(
                        outcome=outcome_all,
                        detail=_stumble_detail,
                        module=str(_ds_exec.get("planned_module") or ""),
                    ),
                )
                if isinstance(_aff, dict) and _aff:
                    pre_ctx["affect_state"] = _aff
                    try:
                        bus.emit_ff(
                            "affect.updated",
                            {
                                "user_id": str(user_id),
                                "affect_state": _aff,
                                "outcome": outcome_all,
                            },
                        )
                    except Exception as e:
                        logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
            except Exception as e:
                logger.debug("affect_state update: %s", e)
            try:
                sm0 = pre_ctx.get("self_model") if isinstance(pre_ctx.get("self_model"), dict) else {}
                sm = update_self_model_after_turn(
                    user_id=str(user_id),
                    base=sm0,
                    outcome=outcome_all,
                    intent=str(_ds_exec.get("last_intent") or ""),
                    module=str(_ds_exec.get("planned_module") or ""),
                    task_tier=str(_ds_exec.get("task_tier") or ""),
                    safe_mode=bool(self._resilience.is_enabled() and self._resilience.is_safe_mode()),
                )
                if isinstance(sm, dict) and sm:
                    pre_ctx["self_model"] = sm
            except Exception as e:
                logger.debug("self_model update: %s", e)
        if plan.steps and user_id:
            try:
                from core.experience_memory import append_experience_record, append_success, experience_enabled

                if experience_enabled() and (user_payload or "").strip():
                    _comb = " ".join(str(o.payload) for o in outputs if o.payload)[:4000]
                    if outcome_all == "ok" and (_comb or "").strip():
                        append_success(
                            user_text=user_payload,
                            intent=str(_ds_exec.get("last_intent") or ""),
                            module=str(_ds_exec.get("planned_module") or ""),
                            planner_reason=str(_ds_exec.get("planner_reason") or ""),
                            assistant_excerpt=_comb,
                            skill_name=_skill_name_exec,
                        )
                        try:
                            from core.strategy_path_memory import append_strategy_success, strategy_path_enabled

                            if strategy_path_enabled():
                                _lap = (
                                    pre_ctx.get("lookahead_plan") if isinstance(pre_ctx, dict) else None
                                )
                                if isinstance(_lap, dict) and _lap.get("steps"):
                                    from core.task_depth import infer_task_tier

                                    append_strategy_success(
                                        user_text=user_payload,
                                        intent=str(_ds_exec.get("last_intent") or ""),
                                        module=str(_ds_exec.get("planned_module") or ""),
                                        task_tier=str(_ds_exec.get("task_tier") or infer_task_tier(user_payload)),
                                        lookahead_plan=_lap,
                                        assistant_excerpt=_comb,
                                        skill_name=_skill_name_exec,
                                    )
                        except Exception as e:
                            logger.debug("strategy_path record: %s", e)
                    elif outcome_all in ("clarify", "failure", "error", "fallback"):
                        _detail_exp = (_stumble_detail or semantic_failure or "")[:400]
                        append_experience_record(
                            user_text=user_payload,
                            intent=str(_ds_exec.get("last_intent") or ""),
                            module=str(_ds_exec.get("planned_module") or ""),
                            planner_reason=str(_ds_exec.get("planner_reason") or ""),
                            outcome=outcome_all,
                            assistant_excerpt=_comb or "",
                            detail=_detail_exp,
                            skill_name=_skill_name_exec,
                        )
            except Exception as e:
                logger.debug("experience_memory record: %s", e)
        if user_id and self.behavior_store and plan.steps:
            combined = " ".join(str(o.payload) for o in outputs if o.payload)[:4000]
            try:
                _patch_st: Dict[str, Any] = {
                    "last_user_excerpt": (user_payload or "")[:240],
                    "last_intent": str(_ds_exec.get("last_intent") or ""),
                    "last_module": str(_ds_exec.get("planned_module") or ""),
                    "last_outcome": outcome_all,
                    "last_skill": str(_skill_name_exec or ""),
                    "last_assistant_excerpt": combined[:480] if combined else "",
                }
                if isinstance(trace_meta, dict):
                    _tid_st = str(trace_meta.get("trace_id") or "").strip()
                    if _tid_st:
                        _patch_st["last_trace_id"] = _tid_st[:64]
                self.behavior_store.patch_session_task(
                    user_id,
                    group_id,
                    _patch_st,
                )
            except Exception as e:
                logger.debug("patch_session_task route: %s", e)
            dialogue_patch = {}
            if pre_ctx.get("dialogue_state"):
                try:
                    from core.brain.discourse_resolver import strip_ephemeral_discourse_state

                    _ds_patch = strip_ephemeral_discourse_state(pre_ctx.get("dialogue_state"))
                except Exception:
                    _ds_patch = pre_ctx.get("dialogue_state") or {}
                dialogue_patch["last_intent"] = (_ds_patch or {}).get("last_intent")
                dialogue_patch["planned_module"] = (_ds_patch or {}).get("planned_module")
                if not group_id:
                    dialogue_patch["assistant_expects_reply"] = infer_assistant_expects_reply(
                        combined,
                        task_tier=str(_ds_exec.get("task_tier") or ""),
                        last_intent=str(_ds_exec.get("last_intent") or ""),
                    )
            psych = pre_ctx.get("psychology") or {}
            micro = _build_micro_emotion_style(psych, pre_ctx.get("behavior_engine") or {})
            tg_admin = bool(pre_ctx.get("telegram_is_admin")) if isinstance(pre_ctx, dict) else False
            turn_meta: Dict[str, Any] = {}
            if plan.steps:
                in_first = (plan.steps[0].args or {}).get("input") or {}
                if isinstance(in_first, dict):
                    m0 = in_first.get("meta") if isinstance(in_first.get("meta"), dict) else {}
                    if isinstance(m0, dict) and m0.get("telegram_message_date_unix") is not None:
                        turn_meta["telegram_message_date_unix"] = m0.get("telegram_message_date_unix")
                    if isinstance(m0, dict) and m0.get("message_id") is not None:
                        try:
                            turn_meta["telegram_message_id"] = int(m0.get("message_id"))
                        except (TypeError, ValueError):
                            pass
            try:
                rec, pending_dc = self.behavior_store.update_after_turn(
                    user_id,
                    group_id,
                    user_payload,
                    combined,
                    dialogue_patch=dialogue_patch,
                    group_patch={"last_turn_has_payload": bool(user_payload)},
                    blended_style=pre_ctx.get("blended_style_stable"),
                    micro_emotion=micro,
                    telegram_is_admin=tg_admin,
                    turn_meta=turn_meta if turn_meta else None,
                )
                # rec уже получен из update_after_turn — второй load не нужен
                if isinstance(pre_ctx.get("cdc_policy"), dict):
                    rec["cdc_policy"] = dict(pre_ctx.get("cdc_policy") or {})
                if isinstance(pre_ctx.get("affect_state"), dict):
                    rec["affect_state"] = dict(pre_ctx.get("affect_state") or {})
                if isinstance(pre_ctx.get("self_model"), dict):
                    rec["self_model"] = dict(pre_ctx.get("self_model") or {})
                if _cdc_policy_patch:
                    rec["cdc_policy"] = _cdc_policy_patch
                goals_patch = self._goal_engine.update_after_turn(
                    persisted=rec,
                    user_text=user_payload,
                    assistant_text=combined,
                )
                rec.update(goals_patch)
                try:
                    from core.user_agent_impression import update_user_agent_impression_in_record

                    update_user_agent_impression_in_record(
                        rec,
                        user_id=str(user_id),
                        user_text=user_payload or "",
                        telegram_is_admin=tg_admin,
                    )
                except Exception as e:
                    logger.debug("user_agent_impression: %s", e)
                # Очищаем инструментальные артефакты перед сохранением —
                # last_tool должен жить только в пределах одного хода (счётчик в impression уже обновлён выше)
                st = rec.get("session_task")
                if isinstance(st, dict):
                    st["last_tool"] = ""
                    st["last_tool_ok"] = None
                    st["last_tool_error"] = ""
                try:
                    if plan.steps:
                        _a_save = getattr(plan.steps[0], "args", None) or {}
                        if isinstance(_a_save, dict) and _a_save.get("fallback_variant") == "news_direct":
                            _ndc = _a_save.get("news_digest_context")
                            if isinstance(_ndc, dict):
                                _ds_save = rec.get("dialogue_state")
                                if not isinstance(_ds_save, dict):
                                    _ds_save = {}
                                    rec["dialogue_state"] = _ds_save
                                if isinstance(_ndc.get("items"), list) and _ndc["items"]:
                                    _ds_save["last_news_digest_items"] = _ndc["items"]
                                if isinstance(_ndc.get("meta"), dict) and _ndc["meta"]:
                                    _ds_save["last_news_digest_meta"] = _ndc["meta"]
                except Exception as e:
                    logger.debug("news_digest_context persist: %s", e)
                try:
                    if combined and re.search(r"(?m)^\d+\.\s+\S", combined):
                        from core.news_reply import stash_parsed_digest_from_assistant

                        stash_parsed_digest_from_assistant(rec, combined)
                except Exception as e:
                    logger.debug("stash_parsed_digest_from_assistant: %s", e)
                self.behavior_store.save(user_id, group_id, rec)
                if pending_dc:
                    spawn_logged(
                        self._dialogue_compact_llm_apply(pending_dc),
                        label="dialogue_compact_llm",
                    )
                try:
                    from core.message_archive import items_for_prompt
                    from core.ops_trace import record_ops_turn

                    _ch = "telegram"
                    if isinstance(trace_meta, dict):
                        _ch = str(
                            trace_meta.get("channel")
                            or trace_meta.get("source")
                            or _ch
                        )
                    _rs_ops: Dict[str, Any] = {}
                    if plan.steps:
                        _cx_ops = (plan.steps[0].args or {}).get("context") or {}
                        if isinstance(_cx_ops, dict):
                            _raw_rs = _cx_ops.get("reasoning_state")
                            if isinstance(_raw_rs, dict):
                                _rs_ops = {
                                    "intent": _raw_rs.get("intent"),
                                    "mode": _raw_rs.get("mode"),
                                    "reason": _raw_rs.get("reason"),
                                }
                    _rb_ops = (
                        pre_ctx.get("recent_dialogue")
                        or pre_ctx.get("recent_messages")
                        or []
                    )
                    record_ops_turn(
                        user_id=str(user_id),
                        group_id=group_id,
                        channel=_ch,
                        user_text=user_payload or "",
                        assistant_text=combined or "",
                        recent_before=_rb_ops,
                        recent_after=rec.get("recent_messages") or [],
                        archive_tail=items_for_prompt(str(user_id), group_id),
                        plan_steps=[s.module_name for s in plan.steps] if plan.steps else [],
                        reasoning=_rs_ops,
                        trace_id=str(trace_meta.get("trace_id") or "") if isinstance(trace_meta, dict) else "",
                        latency_ms=int(_ds_exec.get("total_latency_ms") or 0) if isinstance(_ds_exec, dict) else None,
                        extra={
                            "profile": str(
                                _ds_exec.get("brain_profile")
                                or _ds_exec.get("router_profile")
                                or ""
                            ),
                            "outcome": str(outcome_all or ""),
                        },
                    )
                except Exception as e:
                    logger.debug("ops_trace record: %s", e)
            except Exception as e:
                logger.debug("behavior_store update: %s", e)
            try:
                if user_payload and self.psychology_engine and hasattr(self.psychology_engine, "analyze_message"):
                    self.psychology_engine.analyze_message(str(user_id), user_payload)
            except Exception as e:
                logger.debug("psychology analyze_message: %s", e)
            facts_flow = pre_ctx.get("facts_flow") if isinstance(pre_ctx, dict) else {}
            if isinstance(facts_flow, dict):
                confirm = facts_flow.get("confirmation_prompt")
                if isinstance(confirm, str) and confirm.strip():
                    has_substantive = any(
                        o.type == "text"
                        and len(str(o.payload or "").strip()) >= 80
                        and not bool((o.meta or {}).get("confirmation"))
                        for o in outputs
                    )
                    if has_substantive:
                        confirm = None
                if isinstance(confirm, str) and confirm.strip():
                    from core.clarification_inline_keyboard import fact_confirmation_keyboard_rows
                    from core.telegram_inline_meta import META_KEY

                    outputs.append(
                        Output(
                            type="text",
                            payload=confirm.strip(),
                            meta={
                                "module": "user_facts",
                                "confirmation": True,
                                META_KEY: fact_confirmation_keyboard_rows(),
                            },
                        )
                    )
        try:
            from core.scenario_engine import apply_post_execute, forecast_from_dict, merge_hits

            _sf_exec = forecast_from_dict(
                pre_ctx.get("scenario_forecast") if isinstance(pre_ctx, dict) else None
            )
            _rd_post = None
            _la_post = ""
            if isinstance(pre_ctx, dict):
                _rd_post = pre_ctx.get("recent_dialogue") or pre_ctx.get("recent_messages")
                _ds_post = pre_ctx.get("dialogue_state")
                if isinstance(_ds_post, dict):
                    _la_post = str(_ds_post.get("last_assistant_excerpt") or "").strip()
            outputs, _sc_post, _sc_silent = apply_post_execute(
                outputs,
                user_payload or "",
                _sf_exec,
                recent_dialogue=_rd_post,
                last_assistant=_la_post,
            )
            if isinstance(pre_ctx, dict):
                pre_ctx["_outputs_finalized"] = True
                if _sc_post or _sf_exec.hits:
                    pre_ctx["_scenario_hits"] = merge_hits(_sf_exec, _sc_post)
                    try:
                        from core.scenario_memory import (
                            maybe_autolearn_from_scenarios,
                            record_scenario_hits,
                        )

                        _sids = [h["id"] for h in pre_ctx["_scenario_hits"] if h.get("id")]
                        record_scenario_hits(str(user_id or ""), _sids)
                        maybe_autolearn_from_scenarios(str(user_id or ""), _sids)
                    except Exception as e:
                        logger.debug("scenario_memory: %s", e)
                    try:
                        bus.emit_ff(
                            "turn.scenario",
                            {
                                "user_id": str(user_id or ""),
                                "scenario_hits": pre_ctx["_scenario_hits"],
                            },
                        )
                    except Exception as e:
                        logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                if _sc_silent:
                    pre_ctx["_output_silent_skip"] = True
        except Exception as e:
            logger.debug("scenario_engine finalize: %s", e)
        try:
            if isinstance(pre_ctx, dict):
                _applied = pre_ctx.get("_correction_bus_applied")
                if isinstance(_applied, list) and _applied:
                    from core.user_correction_bus import format_learning_ack_message

                    _ack = format_learning_ack_message(_applied)
                    if _ack:
                        for _oi, _o in enumerate(outputs):
                            if getattr(_o, "type", None) == "text" and str(
                                getattr(_o, "payload", "") or ""
                            ).strip():
                                _meta = dict(getattr(_o, "meta", None) or {})
                                _meta["correction_ack"] = True
                                outputs[_oi] = Output(
                                    type="text",
                                    payload=f"{_ack}\n\n{_o.payload}",
                                    meta=_meta,
                                )
                                break
        except Exception as e:
            logger.debug("correction_ack prepend: %s", e)
        _obs_mark(trace_meta, "exec_done")
        return outputs

    async def _execute_step(self, step: PlanStep, user_id: str, group_id: str, step_index: int = 0) -> List[Output]:
        module_name = step.module_name
        t_start = time.monotonic()
        logger.info(f"[EXEC] step module={module_name}")

        if module_name == "__fallback__":
            variant = (step.args or {}).get("fallback_variant") if isinstance(step.args, dict) else None
            if variant == "empty_payload":
                return [
                    Output(
                        type="text",
                        payload="Пустой запрос. Напиши сообщение или используй команду.",
                        meta={"module": "__fallback__", "reason": "empty_payload"},
                    )
                ]
            if variant == "math_ambiguous":
                from core.telegram_inline_meta import META_KEY

                return [
                    Output(
                        type="text",
                        payload=(
                            "В сообщении есть числа и знаки «+ − * /», но по смыслу это может быть и текст, а не формула.\n\n"
                            "Кнопка «Нет» — обычный ответ без калькулятора.\n"
                            "Кнопка «Да, посчитать» — выделить и посчитать арифметику."
                        ),
                        meta={
                            "module": "__fallback__",
                            "reason": "math_ambiguous",
                            META_KEY: [
                                [
                                    {
                                        "text": "Нет",
                                        "callback_data": "mathamb:skip",
                                    },
                                    {
                                        "text": "Да, посчитать",
                                        "callback_data": "mathamb:calc",
                                    },
                                ]
                            ],
                        },
                    )
                ]
            _dr = (step.args or {}).get("direct_reply") if isinstance(step.args, dict) else None
            _dr_s = str(_dr or "").strip()
            if variant == "news_item_direct":
                _nip = (step.args or {}).get("news_item_pack")
                if isinstance(_nip, dict) and _dr_s:
                    try:
                        from core.news_reply import news_item_outputs_from_pack

                        outs = news_item_outputs_from_pack(_nip)
                        if outs:
                            return outs
                    except Exception as e:
                        logger.debug("news_item_outputs_from_pack: %s", e)
            if _dr_s and variant in _FALLBACK_DIRECT_REPLY_VARIANTS:
                if variant == "article_thread_followup_nl":
                    from core.article_thread_followup import sanitize_article_thread_direct_reply

                    _dr_s = sanitize_article_thread_direct_reply(_dr_s)
                return [
                    Output(
                        type="text",
                        payload=_dr_s,
                        meta={"module": "__fallback__", "reason": variant or "direct_reply"},
                    )
                ]
            if _dr_s and not variant:
                return [
                    Output(
                        type="text",
                        payload=_dr_s,
                        meta={"module": "__fallback__", "reason": "direct_reply"},
                    )
                ]
            ctx_fb = (step.args or {}).get("context") if isinstance(step.args, dict) else None
            ds_fb = (ctx_fb or {}).get("dialogue_state") if isinstance(ctx_fb, dict) else None
            planner_reason = (
                (ds_fb or {}).get("planner_reason") if isinstance(ds_fb, dict) else None
            )
            if planner_reason == "unknown_command":
                return [
                    Output(
                        type="text",
                        payload=(
                            "Такой команды нет. Список ядра и модулей — в /help "
                            "(имя бота после /help может отличаться — всё равно откроется)."
                        ),
                        meta={"module": "__fallback__", "reason": "unknown_command"},
                    )
                ]
            if planner_reason == "no_module_available":
                logger.warning(
                    "[EXEC] __fallback__ no_module_available — проверьте загрузку chat-orchestrator и safe mode"
                )
                return [
                    Output(
                        type="text",
                        payload=(
                            "Сообщение не удалось направить в чат-модуль: диалоговый плагин не был в списке "
                            "разрешённых или не загрузился (часто после safe mode или узкого allowlist). "
                            "Администратору: /admin_health и логи при старте (plugin │ enabled). "
                            "Проверьте SAFE_MODE_MODULE_ALLOWLIST и data/runtime/safe_mode_state.json."
                        ),
                        meta={"module": "__fallback__", "reason": "no_module_available"},
                    )
                ]
            _inp_fb = (step.args or {}).get("input") if isinstance(step.args, dict) else None
            _txt_fb = (
                str((_inp_fb or {}).get("payload") or "").strip()
                if isinstance(_inp_fb, dict)
                else ""
            )
            if _txt_fb and len(_txt_fb) >= 3:
                _payload_fb = (
                    "Не удалось обработать запрос в узком модуле. "
                    "Переформулируйте одной фразой или напишите /help — "
                    "админу: /admin_turns."
                )
            else:
                _payload_fb = "Напишите сообщение или команду из /help."
            return [
                Output(
                    type="text",
                    payload=_payload_fb,
                    meta={"module": "__fallback__", "reason": "default"},
                )
            ]

        module_wrapper = self.plugin_registry.get_module(module_name)
        if not module_wrapper:
            logger.warning(f"[EXEC] module={module_name} not found in registry")
            return [Output(type="text", payload="Модуль недоступен", meta={"module": module_name})]

        if not self.plugin_controller.is_routable(module_name):
            logger.warning("[EXEC] module=%s blocked by plugin_controller denylist", module_name)
            return [
                Output(
                    type="text",
                    payload="Этот плагин временно отключён в конфигурации (PLUGIN_CONTROLLER_DENYLIST).",
                    meta={"module": module_name, "reason": "plugin_controller_denylist"},
                )
            ]

        if module_wrapper.state.status != "healthy":
            logger.warning(f"[EXEC] module={module_name} status={module_wrapper.state.status}")
            return [Output(type="text", payload="Модуль временно недоступен", meta={"module": module_name})]

        args = step.args.copy()
        logger.info(f"[EXEC] calling module={module_name} with keys={list(args.keys())}")

        if self.mem0_memory and user_id:
            try:
                inp = args.get("input") or {}
                payload = str(inp.get("payload", "")) if isinstance(inp, dict) else ""
                ctx0 = args.get("context") or {}
                ctx0 = ctx0 if isinstance(ctx0, dict) else {}
                if ctx0.get("brain_skip_mem0_lookup"):
                    mem0_facts = []
                else:
                    mem0_facts = await self.mem0_memory.on_before_response(user_id, payload)
            except Exception as e:
                logger.debug("mem0 on_before_response: %s", e)
                mem0_facts = []
            ctx0 = args.get("context")
            if not isinstance(ctx0, dict):
                ctx0 = {}
                args["context"] = ctx0
            ctx0["mem0_facts"] = mem0_facts

        MONITOR.inc("module_exec_total")
        t_start = time.monotonic()
        try:
            result = await module_wrapper.instance.execute(args)
        except Exception as exc1:
            MONITOR.inc("module_exec_fail_total")
            bus.emit_ff("module.failed", {
                "module_name": module_name,
                "error": str(exc1)[:500],
                "ok": False,
                "duration_ms": 0.0,
            })
            logger.warning(
                "[EXEC] module=%s first attempt failed: %s — retrying with minimal args",
                module_name, exc1,
            )
            try:
                minimal_args = {"input": args.get("input", {}), "context": args.get("context", {})}
                result = await module_wrapper.instance.execute(minimal_args)
            except Exception as exc2:
                MONITOR.inc("module_exec_fail_total")
                bus.emit_ff("module.failed", {
                    "module_name": module_name,
                    "error": str(exc2)[:500],
                    "ok": False,
                    "traceback": None,
                    "duration_ms": 0.0,
                })
                logger.error("[EXEC] module=%s retry also failed: %s", module_name, exc2)
                # Self-healing: record last_error in reasoning_state so planning_layer
                # can avoid re-calling the same tool on the next step.
                ctx_err = args.get("context") if isinstance(args, dict) else {}
                rs_err = ctx_err.get("reasoning_state") if isinstance(ctx_err, dict) else None
                if isinstance(rs_err, dict):
                    rs_err["last_error"] = {"tool": module_name, "message": str(exc2)[:500], "step_index": step_index}
                # Self-healing 2.0: log tool error with counter
                log_tool_error(module_name, step_index, str(exc2)[:500])
                # LLM Proxy self-healing: register error with resilience
                try:
                    from core.llm_self_heal import detect_tool_error as _proxy_detect_tool_error, apply_recovery_strategy as _proxy_recovery
                    _proxy_resp = {"content": str(exc2)[:500], "tool_calls": []}
                    if _proxy_detect_tool_error(_proxy_resp):
                        _strat = _proxy_recovery("tool_error")
                        if _strat.get("reset_kv"):
                            try:
                                from core.brain.session_stickiness import force_session_reset
                                force_session_reset(user_id=user_id, group_id=group_id, reason="tool_error_proxy")
                            except Exception as e:
                                logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                # Check for auto-reset
                if self_healing_auto_reset():
                    self.context_binder.clear()
                    logger.warning("[SELF_HEALING] context_binder cleared after auto-reset")
                # Autonomy 3.0 episodic memory: log tool error
                try:
                    episodic_record("tool_error", f"{module_name}: {str(exc2)[:200]} step={step_index}", user_id=user_id)
                    self_healing_record_err()
                except Exception as e:
                    logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
                return [
                    Output(
                        type="text",
                        payload=(
                            "Инструмент для этого шага дал ошибку. Продолжаю без него и опишу результат текстом."
                        ),
                        meta={
                            "module": module_name,
                            "reason": "tool_error_self_healing",
                            "error": str(exc2)[:500],
                        },
                    )
                ]
        logger.info(f"[EXEC] module={module_name} returned type={type(result)}")
        exec_ctx = args.get("context") if isinstance(args.get("context"), dict) else {}
        if exec_ctx:
            _sync_brain_context_to_plan_step(step, exec_ctx)
            if isinstance(step.args, dict):
                step.args["context"] = exec_ctx

        # Autonomy 3.0 self-optimization: record response time
        try:
            elapsed = time.monotonic() - t_start if t_start else 0.0
            if elapsed > 0:
                self_healing_record_rt(elapsed)
                telemetry_logger.record_response_time(elapsed, tag=module_name)
        except Exception as e:
            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        if isinstance(result, list):
            out_list: List[Output] = result
        elif isinstance(result, Output):
            out_list = [result]
        else:
            out_list = [Output(type="text", payload=str(result), meta={"module": module_name})]
        MONITOR.inc("module_exec_ok_total")
        elapsed = time.monotonic() - t_start if t_start else 0.0
        bus.emit_ff("module.executed", {
            "module_name": module_name,
            "ok": True,
            "duration_ms": elapsed * 1000,
            "error": None,
        })

        # Autonomy 3.0 episodic memory: log tool success
        try:
            episodic_record("tool_success", f"{module_name}: ok step={step_index}", user_id=user_id)
        except Exception as e:
            logger.debug("%s optional failed: %s", 'orchestrator', e, exc_info=True)
        if self.mem0_memory and user_id:
            try:
                combined = " ".join(str(o.payload) for o in out_list if o.payload)[:4000]
                if combined:
                    await self.mem0_memory.on_after_response(user_id, combined)
            except Exception as e:
                logger.debug("mem0 on_after_response: %s", e)

        return out_list
