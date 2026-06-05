from __future__ import annotations

import logging

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.brain.constants import gemma_instance_author


logger = logging.getLogger(__name__)

def _env_optional_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in {"", "inherit", "default"}:
        return None
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def self_model_enabled() -> bool:
    return _truthy("SELF_MODEL_ENABLED", True)


def prompt_addon_enabled() -> bool:
    """Короткий блок limits в system prompt call_brain (SELF_MODEL_PROMPT_ADDON_ENABLED)."""
    return _truthy("SELF_MODEL_PROMPT_ADDON_ENABLED", True)


def autonomy_style_enabled() -> bool:
    """
    Динамический режим уверенности ответа (только текст в system prompt, без маршрутизации).
    Вкл.: SELF_MODEL_AUTONOMY_STYLE_ENABLED=true
    """
    return _truthy("SELF_MODEL_AUTONOMY_STYLE_ENABLED", default=False)


def autonomy_extended_enabled() -> bool:
    """
    Расширенный dynamic-блок (метрики + доп. строки в промпт). Требует SELF_MODEL_AUTONOMY_STYLE_ENABLED.
    SELF_MODEL_AUTONOMY_EXTENDED_ENABLED=true
    """
    return autonomy_style_enabled() and _truthy("SELF_MODEL_AUTONOMY_EXTENDED_ENABLED", default=False)


def autonomy_goal_context_enabled() -> bool:
    """
    Фаза 1 «автономии действий»: если в context есть autonomy_goal, подмешать цель в system prompt.
    Не меняет маршрутизацию и не считает вызовы — только текст для модели.
    SELF_MODEL_AUTONOMY_GOAL_CONTEXT_ENABLED=true
    """
    return _truthy("SELF_MODEL_AUTONOMY_GOAL_CONTEXT_ENABLED", default=False)


def autonomy_goal_addon_for_prompt(context: Dict[str, Any]) -> str:
    """
    Опционально: context['autonomy_goal'] = {
        'summary' или 'text': краткая цель,
        'max_tool_calls': int (опционально) — мягкий лимит в формулировке промпта,
        'step': str (опционально) — например «3/5» для многошаговых сценариев.
    }
    Заполняет оркестратор/плагин; ядро только отображает в промпте.
    """
    if not autonomy_goal_context_enabled() or not isinstance(context, dict):
        return ""
    g = context.get("autonomy_goal")
    if not isinstance(g, dict):
        return ""
    summary = str(g.get("summary") or g.get("text") or "").strip()
    if not summary:
        return ""
    summary = summary[:800]
    chunks = []
    step = str(g.get("step") or "").strip()
    if step:
        chunks.append(f"Автономия (цель, шаг {step}): {summary}")
    else:
        chunks.append(f"Автономия (цель сессии): {summary}")
    mtc = g.get("max_tool_calls")
    if mtc is not None:
        try:
            n = int(mtc)
            if n > 0:
                chunks.append(
                    f"Не делай более {n} вызовов инструментов подряд без явного продвижения к цели; "
                    "если упираешься — коротко опиши стопор и предложи упростить или сузить задачу."
                )
        except (TypeError, ValueError):
            pass
    return " ".join(chunks).strip()


def hydrate_autonomy_goal_from_runtime(
    ctx: Dict[str, Any],
    *,
    user_text: str,
    goal_hints: Optional[Dict[str, Any]] = None,
    lookahead_plan: Optional[Dict[str, Any]] = None,
    planned_intent: str = "",
    task_tier: str = "",
) -> None:
    """
    Заполняет context['autonomy_goal'], если включено SELF_MODEL_AUTONOMY_GOAL_CONTEXT_ENABLED
    и плагин ещё не задал свой autonomy_goal.
    Источники: mission/active_goals, первый шаг lookahead, иначе длинное сообщение пользователя.
    """
    if not autonomy_goal_context_enabled() or not isinstance(ctx, dict):
        return
    ex = ctx.get("autonomy_goal")
    if isinstance(ex, dict) and str(ex.get("summary") or ex.get("text") or "").strip():
        return

    parts: List[str] = []
    gh = goal_hints if isinstance(goal_hints, dict) else {}
    mission = str(gh.get("mission") or "").strip()
    if mission:
        parts.append(mission)
    ag = gh.get("active_goals") or []
    if isinstance(ag, list):
        for g in ag[:3]:
            if isinstance(g, dict):
                t = str(g.get("text") or "").strip()
                if t:
                    parts.append(t)

    lap = lookahead_plan if isinstance(lookahead_plan, dict) else {}
    steps = lap.get("steps")
    if isinstance(steps, list) and steps:
        first = steps[0]
        if isinstance(first, dict):
            d = str(first.get("do") or "").strip()
            if d:
                parts.append(f"Ближайший шаг: {d[:280]}")

    summary = " · ".join(x for x in parts if x)
    ut = (user_text or "").strip()
    if not summary and len(ut) >= 120:
        summary = ut[:500]
    if not summary:
        return

    prefix = str(planned_intent or "").strip()
    if prefix and prefix.lower() != "general":
        summary = f"[{prefix}] {summary}"

    try:
        mtc = int((os.getenv("SELF_MODEL_AUTONOMY_GOAL_DEFAULT_MAX_TOOL_CALLS") or "8").strip() or "8")
    except ValueError:
        mtc = 8
    mtc = max(1, min(mtc, 24))

    step_meta = str(task_tier or "").strip()
    out: Dict[str, Any] = {"summary": summary.strip(), "max_tool_calls": mtc}
    if step_meta:
        out["step"] = f"tier={step_meta}"
    ctx["autonomy_goal"] = out


def _autonomy_lookback_size() -> int:
    try:
        lookback = int((os.getenv("SELF_MODEL_AUTONOMY_LOOKBACK") or "5").strip() or "5")
    except ValueError:
        lookback = 5
    return max(2, min(lookback, 30))


def _autonomy_tail(sm: Dict[str, Any]) -> tuple[list, int, float, int]:
    """tail rows, lookback, confidence score, clarify count."""
    cs = sm.get("confidence_summary") if isinstance(sm.get("confidence_summary"), dict) else {}
    try:
        score = float(cs.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.05, min(0.99, score))
    lookback = _autonomy_lookback_size()
    recent = sm.get("recent_outcomes") if isinstance(sm.get("recent_outcomes"), list) else []
    tail = [x for x in recent[-lookback:] if isinstance(x, dict)]
    clarify_n = sum(1 for x in tail if str(x.get("outcome") or "").strip().lower() == "clarify")
    return tail, lookback, score, clarify_n


def compute_extended_dynamic_block(sm: Dict[str, Any]) -> Dict[str, Any]:
    """
    Диагностические поля для KV/отладки и доп. строк в промпт.
    Только сигналы из self_model (исходы ходов, limits) — без CDC/репутации и без NLP по тексту пользователя.
    """
    tail, lookback, score, clarify_n = _autonomy_tail(sm)
    n = len(tail)
    clarify_rate = round((float(clarify_n) / float(max(1, n))), 3) if n else 0.0
    context_stability = max(0.0, min(1.0, 1.0 - (float(clarify_n) / float(max(1, lookback)))))

    bad_out = frozenset({"fallback", "failure", "error"})
    bad_n = sum(1 for x in tail if str(x.get("outcome") or "").strip().lower() in bad_out)
    bad_rate = float(bad_n) / float(max(1, n)) if n else 0.0

    last_intent = ""
    if tail:
        last_intent = str(tail[-1].get("intent") or "").strip().lower()
    intent_hits = (
        sum(1 for x in tail if str(x.get("intent") or "").strip().lower() == last_intent) if n and last_intent else n
    )
    intent_stability = float(intent_hits) / float(max(1, n)) if n else 1.0

    dialog_health = max(
        0.0,
        min(
            1.0,
            0.35 * (1.0 - bad_rate) + 0.35 * context_stability + 0.30 * intent_stability,
        ),
    )

    trust_memory = max(0.05, min(0.99, score * (1.0 - 0.65 * clarify_rate)))

    lim = sm.get("limits") if isinstance(sm.get("limits"), dict) else {}
    nf = bool(lim.get("no_force_external_state", True))
    base_ext = 0.58 if nf else 0.84
    trust_external = max(0.05, min(0.99, base_ext * (0.75 + 0.25 * (1.0 - bad_rate))))

    lr = sm.get("last_route") if isinstance(sm.get("last_route"), dict) else {}
    mod = str(lr.get("module") or "").strip()
    mod_rows = [x for x in tail if str(x.get("module") or "").strip() == mod] if mod else list(tail)
    mden = len(mod_rows) if mod_rows else n
    mod_bad = sum(1 for x in (mod_rows if mod_rows else tail) if str(x.get("outcome") or "").strip().lower() in bad_out or str(x.get("outcome") or "").strip().lower() == "clarify")
    mod_bad_rate = float(mod_bad) / float(max(1, mden)) if mden else bad_rate
    trust_module = max(0.05, min(0.99, 1.0 - 0.85 * mod_bad_rate))

    return {
        "auto_confidence": round(score, 3),
        "context_stability": round(context_stability, 3),
        "trust_state": {
            "memory": round(trust_memory, 3),
            "external_data": round(trust_external, 3),
            "active_module": round(trust_module, 3),
        },
        "clarify_rate": clarify_rate,
        "dialog_health": round(dialog_health, 3),
    }


def compute_response_style_mode(sm: Dict[str, Any]) -> str:
    """
    assertive | normal | cautious — по confidence_summary.score и числу clarify в последних ходах.
    Не использует CDC, репутацию и tier (избегаем двойного наказания с планировщиком).
    """
    cs = sm.get("confidence_summary") if isinstance(sm.get("confidence_summary"), dict) else {}
    try:
        score = float(cs.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.05, min(0.99, score))

    lookback = _autonomy_lookback_size()

    try:
        thr_as = float((os.getenv("SELF_MODEL_AUTONOMY_SCORE_ASSERTIVE") or "0.8").strip() or "0.8")
    except ValueError:
        thr_as = 0.8
    try:
        thr_cau = float((os.getenv("SELF_MODEL_AUTONOMY_SCORE_CAUTIOUS") or "0.5").strip() or "0.5")
    except ValueError:
        thr_cau = 0.5
    thr_as = max(0.1, min(0.95, thr_as))
    thr_cau = max(0.05, min(thr_as - 0.05, thr_cau))

    try:
        clarify_bad = int((os.getenv("SELF_MODEL_AUTONOMY_CLARIFY_CAUTIOUS") or "2").strip() or "2")
    except ValueError:
        clarify_bad = 2
    clarify_bad = max(1, min(clarify_bad, lookback))

    try:
        min_turns_boost = int((os.getenv("SELF_MODEL_AUTONOMY_MIN_TURNS_ASSERTIVE") or "3").strip() or "3")
    except ValueError:
        min_turns_boost = 3
    min_turns_boost = max(1, min(min_turns_boost, lookback))

    tail, _, score, clarify_n = _autonomy_tail(sm)

    if clarify_n >= clarify_bad or score < thr_cau:
        return "cautious"
    if score > thr_as:
        return "assertive"
    if clarify_n == 0 and len(tail) >= min_turns_boost and score >= thr_cau:
        return "assertive"
    return "normal"


def _autonomy_style_addon_line(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m == "assertive":
        return (
            "Режим ответа: по метрикам последних ходов маршрутизация шла без сбоев — формулируй ясно и не раздувай "
            "мета-оговорки про работу бота. Это не оценка истинности фактов: при сомнении проверяй инструментами; "
            "если пользователь поправляет — принимай правку, не спорь ради «уверенного» тона."
        )
    if m == "cautious":
        return "Режим ответа: возможны пробелы в контексте — формулируй аккуратно, при необходимости уточни важное одним коротким вопросом."
    return "Режим ответа: обычный баланс между ясностью и осторожностью."


def _persist_dynamic_style(sm: Dict[str, Any]) -> Dict[str, Any]:
    if not autonomy_style_enabled():
        return sm
    mode = compute_response_style_mode(sm)
    dyn = dict(sm.get("dynamic") or {}) if isinstance(sm.get("dynamic"), dict) else {}
    dyn["response_style"] = mode
    if autonomy_extended_enabled():
        ext = compute_extended_dynamic_block(sm)
        for k, v in ext.items():
            dyn[k] = v
    else:
        for k in (
            "auto_confidence",
            "context_stability",
            "trust_state",
            "clarify_rate",
            "dialog_health",
        ):
            dyn.pop(k, None)
    sm = dict(sm)
    sm["dynamic"] = dyn
    return sm


def _ext_low_threshold(name: str, default: float) -> float:
    try:
        v = float((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        v = default
    return max(0.05, min(0.95, v))


def _extended_trust_addon_lines(sm: Dict[str, Any]) -> list:
    if not autonomy_extended_enabled():
        return []
    block = compute_extended_dynamic_block(sm)
    thr_trust = _ext_low_threshold("SELF_MODEL_AUTONOMY_EXT_TRUST_LOW", 0.45)
    thr_ctx = _ext_low_threshold("SELF_MODEL_AUTONOMY_EXT_CONTEXT_LOW", 0.42)
    thr_health = _ext_low_threshold("SELF_MODEL_AUTONOMY_EXT_HEALTH_LOW", 0.48)
    ts = block.get("trust_state") if isinstance(block.get("trust_state"), dict) else {}
    lines = []
    try:
        if float(ts.get("memory", 1.0)) < thr_trust:
            lines.append(
                "Саморегуляция: последние исходы указывают на низкую устойчивость интерпретации — ключевые факты при сомнении лучше подтвердить коротко."
            )
        if float(ts.get("external_data", 1.0)) < thr_trust:
            lines.append(
                "Саморегуляция: внешним подсказкам и данным из API в этой фазе стоит доверять осторожнее; при риске ошибки используй инструменты проверки."
            )
        if float(block.get("context_stability", 1.0)) < thr_ctx:
            lines.append(
                "Саморегуляция: контекст диалога выглядит разреженным (много уточнений) — держи ответ ясным и при необходимости уточни одну критичную деталь."
            )
        if float(block.get("dialog_health", 1.0)) < thr_health:
            lines.append(
                "Саморегуляция: по метрикам последних ходов диалог «шумный» (сбои/смены) — избегай категоричности без опоры на факты из инструментов."
            )
    except (TypeError, ValueError):
        return []
    return lines


def merge_limits_effective(sm: Dict[str, Any]) -> Dict[str, Any]:
    """
    Подмешивает limits из self_model и опциональные переопределения .env:
    SELF_MODEL_NO_FORCE_EXTERNAL_STATE, SELF_MODEL_CONTEXT_IS_PROBABILISTIC.
    Если переменная не задана — сохраняются значения из sm.limits, иначе дефолт True.
    """
    out = dict(sm or {})
    lim = dict(out["limits"]) if isinstance(out.get("limits"), dict) else {}
    v_nf = _env_optional_bool("SELF_MODEL_NO_FORCE_EXTERNAL_STATE")
    v_cp = _env_optional_bool("SELF_MODEL_CONTEXT_IS_PROBABILISTIC")
    if v_nf is not None:
        lim["no_force_external_state"] = bool(v_nf)
    elif "no_force_external_state" not in lim:
        lim["no_force_external_state"] = True
    if v_cp is not None:
        lim["context_is_probabilistic"] = bool(v_cp)
    elif "context_is_probabilistic" not in lim:
        lim["context_is_probabilistic"] = True
    out["limits"] = lim
    return out


def self_model_trust_addon_for_prompt(self_model: Optional[Dict[str, Any]]) -> str:
    """
    Одно–два предложения в system prompt: как интерпретировать внешний контекст и память.
    Учитывает merge_limits_effective (.env может переопределить limits).
    """
    if not self_model_enabled() or not prompt_addon_enabled():
        return ""
    sm = merge_limits_effective(dict(self_model) if isinstance(self_model, dict) else {})
    limits = sm.get("limits") if isinstance(sm.get("limits"), dict) else {}
    nf = bool(limits.get("no_force_external_state", True))
    cp = bool(limits.get("context_is_probabilistic", True))
    ext = (
        "Внешние сведения (API, статусы, подсказки оркестратора) не считай абсолютной истиной без проверки инструментами, "
        "если от этого зависит точность или безопасность."
        if nf
        else "Опирайся на внешние подсказки и контекст задачи напрямую; не раздувай оговорки о ненадёжности без повода."
    )
    mem = (
        "Фрагменты долговременной памяти и сжатой истории могут быть неполными — при явном противоречии с пользователем "
        "приоритет у формулировки в текущем сообщении."
        if cp
        else "Историю диалога и сохранённые факты считай согласованными, пока пользователь явно не поправляет или не отменяет."
    )
    parts = [f"{ext} {mem}".strip()]
    if autonomy_style_enabled():
        mode = compute_response_style_mode(sm)
        parts.append(_autonomy_style_addon_line(mode))
        parts.extend(_extended_trust_addon_lines(sm))
    return " ".join(p for p in parts if p).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_int(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    raw = os.getenv(name)
    try:
        val = int((raw or "").strip()) if raw is not None else int(default)
    except Exception:
        val = int(default)
    return max(minimum, min(maximum, val))


def _outcome_score(outcome: str) -> float:
    o = (outcome or "").strip().lower()
    if o == "ok":
        return 1.0
    if o in {"fallback", "partial"}:
        return 0.4
    if o in {"failure", "error"}:
        return -0.6
    return 0.0


def _append_recent(window: list, item: Dict[str, Any], max_len: int) -> list:
    seq = list(window or [])
    seq.append(item)
    if len(seq) > max_len:
        seq = seq[-max_len:]
    return seq


def default_self_model() -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "version": 1,
        "identity": {
            "agent_role": "autonomous_assistant",
            "platform": "gemma_bot",
            "runtime": "orchestrator",
            "instance_author": gemma_instance_author(),
        },
        "capabilities": {
            "tool_use": True,
            "plugin_routing": True,
            "self_maintenance": True,
        },
        "limits": {
            "no_force_external_state": True,
            "context_is_probabilistic": True,
        },
        "confidence_summary": {
            "score": 0.5,
            "trend": "stable",
            "window_size": 0,
        },
        "recent_outcomes": [],
        "active_constraints": [],
        "last_updated": _now_iso(),
    }
    return merge_limits_effective(base)


def hydrate_self_model_from_kv(user_id: str, persisted: Dict[str, Any]) -> Dict[str, Any]:
    rec = dict(persisted or {})
    if not self_model_enabled():
        return rec
    cur = rec.get("self_model")
    if isinstance(cur, dict) and cur:
        rec["self_model"] = merge_limits_effective(cur)
        return rec
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json

        if user_id and agent_kv_enabled():
            sm = get_json("self_model", str(user_id), branch=agent_kv_branch()) or {}
            if isinstance(sm, dict) and sm:
                rec["self_model"] = merge_limits_effective(sm)
                return rec
    except Exception as e:
        logger.debug('%s optional failed: %s', 'self_model', e, exc_info=True)
    rec["self_model"] = default_self_model()
    return rec


def update_self_model_after_turn(
    *,
    user_id: str,
    base: Optional[Dict[str, Any]],
    outcome: str,
    intent: str,
    module: str,
    task_tier: str,
    safe_mode: bool,
) -> Dict[str, Any]:
    if not self_model_enabled():
        return base if isinstance(base, dict) else default_self_model()
    sm = dict(base) if isinstance(base, dict) and base else default_self_model()
    sm["last_updated"] = _now_iso()
    sm["last_route"] = {
        "intent": (intent or "").strip(),
        "module": (module or "").strip(),
        "task_tier": (task_tier or "").strip(),
        "outcome": (outcome or "").strip(),
    }
    win = _parse_int("SELF_MODEL_TREND_WINDOW", 12, minimum=3, maximum=120)
    recent = sm.get("recent_outcomes") if isinstance(sm.get("recent_outcomes"), list) else []
    cur_item = {
        "ts": sm["last_updated"],
        "outcome": (outcome or "").strip().lower(),
        "intent": (intent or "").strip().lower(),
        "module": (module or "").strip(),
        "task_tier": (task_tier or "").strip(),
        "score": _outcome_score(outcome),
    }
    recent = _append_recent(recent, cur_item, win)
    sm["recent_outcomes"] = recent
    scores = [float(x.get("score", 0.0)) for x in recent if isinstance(x, dict)]
    avg = (sum(scores) / float(len(scores))) if scores else 0.0
    if avg >= 0.6:
        trend = "up"
    elif avg <= -0.15:
        trend = "down"
    else:
        trend = "stable"
    conf = max(0.05, min(0.99, 0.5 + (avg * 0.45)))
    sm["confidence_summary"] = {
        "score": round(conf, 3),
        "trend": trend,
        "window_size": len(scores),
    }
    constraints = set(sm.get("active_constraints") if isinstance(sm.get("active_constraints"), list) else [])
    if safe_mode:
        constraints.add("safe_mode")
    else:
        constraints.discard("safe_mode")
    if (outcome or "").strip() in {"fallback", "failure", "error"}:
        constraints.add("recovery_bias")
    else:
        constraints.discard("recovery_bias")
    # Trend-aware constraint: sustained degradation tightens behavior even without hard safe mode.
    if trend == "down":
        constraints.add("stability_guard")
    else:
        constraints.discard("stability_guard")
    sm["active_constraints"] = sorted(constraints)
    sm = merge_limits_effective(sm)
    sm = _persist_dynamic_style(sm)
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, set_json

        if user_id and agent_kv_enabled():
            ttl = int((os.getenv("SELF_MODEL_TTL_SEC") or "2592000").strip() or "2592000")
            set_json("self_model", str(user_id), sm, branch=agent_kv_branch(), ttl_sec=ttl, priority=15)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'self_model', e, exc_info=True)
    return sm
