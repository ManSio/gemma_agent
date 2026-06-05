"""
Фаза 1 CDC: формализованный исход хода (reward), агрегаты по (user, module, intent),
политика в BehaviorStore — потолок tier по маршруту и штраф модуля (перевод в диалог).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from core.experience_memory import fingerprint, normalize_module_key
from core.monitoring import MONITOR
from core.route_risk_memory import classify_error_type
from core.runtime_telegram_settings import effective_bool
from core.unified_planner import pick_dialog_module

logger = logging.getLogger(__name__)

_AGG_LOCK = threading.Lock()

_OUTCOME_REWARD = {
    "ok": 1.0,
    "clarify": 0.0,
    "fallback": -0.5,
    "error": -1.0,
    "failure": -1.0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cdc_enabled() -> bool:
    return effective_bool("CDC_ENGINE_ENABLED", default=False)


def _turn_log_path() -> str:
    p = (os.getenv("GEMMA_CDC_TURN_LOG") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "cdc_turn_outcomes.jsonl")


def _aggregates_path() -> str:
    p = (os.getenv("GEMMA_CDC_AGGREGATES_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "cdc_aggregates.json")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        return default


def outcome_reward(outcome: str) -> float:
    return float(_OUTCOME_REWARD.get((outcome or "").strip(), -0.25))


def _agg_key(user_id: str, module: str, intent: str) -> str:
    u = str(user_id or "").strip()
    m = normalize_module_key(module)
    i = (intent or "").strip() or "unknown"
    return f"{u}|{m}|{i}"


def _skill_agg_key(user_id: str, skill_name: str) -> str:
    u = str(user_id or "").strip()
    s = (skill_name or "").strip()
    if not u or not s:
        return ""
    return f"{u}|{s}"


def _reputation_from_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v_c": bucket.get("v_c"),
        "v_p": bucket.get("v_p"),
        "fail_streak": bucket.get("fail_streak"),
        "success_streak": bucket.get("success_streak"),
        "n_ok": bucket.get("n_ok"),
        "n_bad": bucket.get("n_bad"),
        "n_turns": bucket.get("n_turns"),
        "reward_ema": bucket.get("reward_ema"),
        "updated_ts": bucket.get("updated_ts"),
    }


def _empty_aggregate() -> Dict[str, Any]:
    return {
        "n_turns": 0,
        "n_ok": 0,
        "n_bad": 0,
        "fail_streak": 0,
        "success_streak": 0,
        "reward_ema": 0.0,
        "v_c": 0.5,
        "v_p": 0.5,
    }


def _persist_reputation_aggregate(
    *,
    key: str,
    outcome: str,
    reward: float,
    cdc_ns: str,
    rep_ns: str,
    kv_ok: bool,
    br: str,
    get_json: Any,
    set_json: Any,
    ttl: Optional[int],
    path_agg: str,
) -> None:
    """Обновить агрегат и зеркало reputation (маршрут или скилл)."""
    if not key:
        return
    bucket: Dict[str, Any] = {}
    if kv_ok and get_json:
        existing = get_json(cdc_ns, key, branch=br)
        bucket = dict(existing) if isinstance(existing, dict) else {}
    else:
        all_ag = _load_aggregates_unlocked(path_agg)
        bucket = dict(all_ag.get(key) or {}) if isinstance(all_ag.get(key), dict) else {}
    if not bucket:
        bucket = _empty_aggregate()
    _bump_aggregate(bucket, outcome, reward)
    if kv_ok and set_json:
        set_json(cdc_ns, key, bucket, branch=br, ttl_sec=ttl, priority=50)
        set_json(
            rep_ns,
            key,
            _reputation_from_bucket(bucket),
            branch=br,
            ttl_sec=ttl,
            priority=40,
        )
    elif _env_truthy("CDC_AGG_JSON_MIRROR", True):
        all_ag = _load_aggregates_unlocked(path_agg)
        all_ag[key] = bucket
        _save_aggregates_unlocked(path_agg, all_ag)


def _load_aggregates_unlocked(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        logger.debug("cdc aggregates load: %s", e)
        return {}


def _save_aggregates_unlocked(path: str, data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except OSError:
        pass
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.debug("cdc aggregates save: %s", e)


def _bump_aggregate(agg: Dict[str, Any], outcome: str, reward: float) -> None:
    o = (outcome or "").strip()
    agg["n_turns"] = int(agg.get("n_turns") or 0) + 1
    if o == "ok":
        agg["n_ok"] = int(agg.get("n_ok") or 0) + 1
        agg["fail_streak"] = 0
        agg["success_streak"] = int(agg.get("success_streak") or 0) + 1
    else:
        agg["n_bad"] = int(agg.get("n_bad") or 0) + 1
        agg["fail_streak"] = int(agg.get("fail_streak") or 0) + 1
        agg["success_streak"] = 0
    alpha = max(0.01, min(0.99, _env_float("CDC_REWARD_EMA_ALPHA", 0.3)))
    old_ema = float(agg.get("reward_ema") or 0.0)
    agg["reward_ema"] = alpha * reward + (1.0 - alpha) * old_ema
    agg["updated_ts"] = _now_iso()
    # Репутация v_c / v_p (кооперативный vs наказанный поток), 0..1
    beta = max(0.05, min(0.6, _env_float("CDC_REPUTATION_EMA_ALPHA", 0.25)))
    vc = float(agg.get("v_c") if agg.get("v_c") is not None else 0.5)
    vp = float(agg.get("v_p") if agg.get("v_p") is not None else 0.5)
    if o == "ok":
        vc = beta * 1.0 + (1.0 - beta) * vc
        vp = (1.0 - beta) * vp
    else:
        vp = beta * 1.0 + (1.0 - beta) * vp
        vc = (1.0 - beta) * vc
    agg["v_c"] = max(0.0, min(1.0, vc))
    agg["v_p"] = max(0.0, min(1.0, vp))


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_policy_for_user(user_id: str, aggregates: Dict[str, Any]) -> Dict[str, Any]:
    """Строит cdc_policy для сессии пользователя из глобальных агрегатов."""
    prefix = str(user_id or "").strip() + "|"
    tier_streak = max(1, _env_int("CDC_FAIL_STREAK_TIER_CAP", 3))
    mod_streak = max(tier_streak, _env_int("CDC_FAIL_STREAK_MODULE_PENALTY", 4))
    disable_streak = max(mod_streak, _env_int("CDC_ROUTE_DISABLE_STREAK", 6))
    route_tier_caps: Dict[str, str] = {}
    disabled_routes: List[str] = []
    mod_max_fail: Dict[str, int] = {}
    for k, v in aggregates.items():
        if not isinstance(k, str) or not k.startswith(prefix):
            continue
        parts = k.split("|", 2)
        if len(parts) != 3:
            continue
        _, mod, intent = parts
        if not isinstance(v, dict):
            continue
        fs = int(v.get("fail_streak") or 0)
        if fs >= tier_streak:
            route_tier_caps[f"{mod}|{intent}"] = "nested"
        if fs >= disable_streak:
            disabled_routes.append(f"{mod}|{intent}")
        mod_max_fail[mod] = max(mod_max_fail.get(mod, 0), fs)
    penalized = sorted(m for m, fs in mod_max_fail.items() if fs >= mod_streak)
    return {
        "route_tier_caps": route_tier_caps,
        "disabled_routes": sorted(set(disabled_routes)),
        "penalized_modules": penalized,
        "updated_ts": _now_iso(),
    }


def classify_reaction_level(*, bucket: Dict[str, Any], outcome: str, error_type: str) -> str:
    """
    local: разовый/локальный сбой;
    route: маршрут стабильно даёт bad;
    policy: системная деградация поведения (высокий v_p/длинная серия).
    """
    fs = int(bucket.get("fail_streak") or 0)
    n_bad = int(bucket.get("n_bad") or 0)
    vp = float(bucket.get("v_p") or 0.0)
    try:
        route_bad = max(2, _env_int("CDC_ROUTE_PROBLEM_N_BAD", 4))
        policy_fs = max(3, _env_int("CDC_POLICY_PROBLEM_FAIL_STREAK", 7))
        policy_vp = max(0.2, min(1.0, _env_float("CDC_POLICY_PROBLEM_VP", 0.75)))
    except Exception:
        route_bad, policy_fs, policy_vp = 4, 7, 0.75
    if (error_type or "") == "policy" or fs >= policy_fs or vp >= policy_vp:
        return "policy"
    if n_bad >= route_bad or fs >= max(2, _env_int("CDC_ROUTE_PROBLEM_FAIL_STREAK", 4)):
        return "route"
    if (outcome or "").strip() in ("error", "failure", "fallback", "clarify"):
        return "local"
    return "none"


def _apply_reaction_to_policy(
    policy: Dict[str, Any],
    *,
    reaction_level: str,
    module: str,
    intent: str,
    error_type: str,
) -> Dict[str, Any]:
    out = dict(policy or {})
    out["reaction_level"] = reaction_level
    out["last_error_type"] = (error_type or "unknown")
    out["last_reaction_ts"] = _now_iso()
    route_key = f"{normalize_module_key(module)}|{(intent or '').strip() or 'unknown'}"
    if reaction_level == "local":
        out["next_turn_tier_floor"] = "nested"
        out["route_hint_level"] = "strong"
    elif reaction_level == "route":
        pen = set(str(x) for x in (out.get("penalized_modules") or []) if str(x).strip())
        pen.add(normalize_module_key(module))
        out["penalized_modules"] = sorted(pen)
        dis = set(str(x) for x in (out.get("disabled_routes") or []) if str(x).strip())
        if _env_truthy("CDC_DISABLE_ROUTE_ON_ROUTE_PROBLEM", True):
            dis.add(route_key)
        out["disabled_routes"] = sorted(dis)
    elif reaction_level == "policy":
        out["grim_force_dialog"] = True
        out["grim_tier_ceiling"] = "shallow"
    return out


def apply_user_feedback_to_cdc(
    *,
    user_id: str,
    user_text: str,
    intent: str,
    module: str,
    positive: bool,
    skill_name: str = "",
) -> bool:
    """Ручная оценка пользователя → тот же агрегат reputation без полного хода."""
    if not cdc_enabled():
        return False
    outcome = "ok" if positive else "fallback"
    try:
        process_turn_end(
            user_id=user_id,
            user_text=user_text or "[user_feedback]",
            intent=intent,
            module=module,
            outcome=outcome,
            task_tier="",
            detail="user_feedback",
            error_type="user" if not positive else "",
            skill_name=skill_name,
        )
        return True
    except Exception as e:
        logger.debug("apply_user_feedback_to_cdc: %s", e)
        return False


def process_turn_end(
    *,
    user_id: str,
    user_text: str,
    intent: str,
    module: str,
    outcome: str,
    task_tier: str = "",
    detail: str = "",
    error_type: str = "",
    skill_name: str = "",
) -> Dict[str, Any]:
    """
    После хода: JSONL-событие, обновление агрегатов, новая политика для merge в BehaviorStore.
    """
    if not cdc_enabled():
        return {}
    uid = str(user_id or "").strip()
    if not uid:
        return {}
    path_agg = _aggregates_path()
    key = _agg_key(uid, module, intent)
    reward = outcome_reward(outcome)
    fp = fingerprint(user_text)
    err_type = (error_type or classify_error_type(outcome=outcome, detail=detail, module=module)).strip() or "unknown"
    line = {
        "ts": _now_iso(),
        "user_id": uid,
        "fp": fp,
        "intent": (intent or "").strip() or "unknown",
        "module": normalize_module_key(module),
        "outcome": (outcome or "").strip(),
        "reward": reward,
        "task_tier": (task_tier or "").strip(),
        "detail": (detail or "")[:160],
        "error_type": err_type,
        "skill": (skill_name or "").strip() or None,
        "policy_version_before": None,
        "policy_version_after": None,
    }
    policy: Dict[str, Any] = {}
    skill_key = _skill_agg_key(uid, skill_name)
    with _AGG_LOCK:
        kv_ok = False
        br = "main"
        _get_json = None
        _set_json = None
        _iter_prefix = None
        _merge_grim = None
        _update_grim = None
        try:
            from core.agent_kv.grim import merge_grim_policy_into, update_grim_after_turn
            from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json, iter_prefix, set_json

            if agent_kv_enabled():
                kv_ok = True
                br = agent_kv_branch()
                _get_json = get_json
                _set_json = set_json
                _iter_prefix = iter_prefix
                _merge_grim = merge_grim_policy_into
                _update_grim = update_grim_after_turn
        except Exception as e:
            logger.debug("cdc agent_kv: %s", e)

        bucket = {}
        if kv_ok and _get_json:
            existing = _get_json("cdc_agg", key, branch=br)
            bucket = dict(existing) if isinstance(existing, dict) else {}
        else:
            all_ag = _load_aggregates_unlocked(path_agg)
            bucket = dict(all_ag.get(key) or {}) if isinstance(all_ag.get(key), dict) else {}

        if not bucket:
            bucket = _empty_aggregate()
        _bump_aggregate(bucket, outcome, reward)
        reaction_level = classify_reaction_level(bucket=bucket, outcome=outcome, error_type=err_type)

        if kv_ok and _set_json and _iter_prefix and _merge_grim and _update_grim and _get_json:
            prev_pol = _get_json("cdc_policy", uid, branch=br) or {}
            try:
                line["policy_version_before"] = int(prev_pol.get("_version")) if prev_pol.get("_version") is not None else None
            except (TypeError, ValueError):
                line["policy_version_before"] = None
            try:
                ttl_agg = int((os.getenv("CDC_AGG_KV_TTL_SEC") or "0").strip() or "0")
            except ValueError:
                ttl_agg = 0
            ttl = ttl_agg if ttl_agg > 0 else None
            _set_json("cdc_agg", key, bucket, branch=br, ttl_sec=ttl, priority=50)
            _set_json(
                "reputation",
                key,
                _reputation_from_bucket(bucket),
                branch=br,
                ttl_sec=ttl,
                priority=40,
            )
            partial = dict(_iter_prefix("cdc_agg", f"{uid}|", branch=br))
            policy = build_policy_for_user(uid, partial)
            policy = _apply_reaction_to_policy(
                policy,
                reaction_level=reaction_level,
                module=module,
                intent=intent,
                error_type=err_type,
            )
            _update_grim(
                uid,
                outcome=outcome,
                agg_bucket=bucket,
                module=module,
                intent=intent,
            )
            policy = _merge_grim(policy, _get_json("grim", uid, branch=br))
            pver = _set_json("cdc_policy", uid, policy, branch=br, ttl_sec=ttl, priority=60)
            try:
                line["policy_version_after"] = int(pver)
            except (TypeError, ValueError):
                line["policy_version_after"] = None
            if _env_truthy("CDC_AGG_JSON_MIRROR", True):
                all_ag = _load_aggregates_unlocked(path_agg)
                all_ag[key] = bucket
                _save_aggregates_unlocked(path_agg, all_ag)
        else:
            all_ag = _load_aggregates_unlocked(path_agg)
            all_ag[key] = bucket
            _save_aggregates_unlocked(path_agg, all_ag)
            policy = build_policy_for_user(uid, all_ag)
            policy = _apply_reaction_to_policy(
                policy,
                reaction_level=reaction_level,
                module=module,
                intent=intent,
                error_type=err_type,
            )
        if skill_key:
            try:
                ttl_skill = None
                if kv_ok:
                    try:
                        ttl_agg = int((os.getenv("CDC_AGG_KV_TTL_SEC") or "0").strip() or "0")
                    except ValueError:
                        ttl_agg = 0
                    ttl_skill = ttl_agg if ttl_agg > 0 else None
                _persist_reputation_aggregate(
                    key=skill_key,
                    outcome=outcome,
                    reward=reward,
                    cdc_ns="cdc_agg_skill",
                    rep_ns="reputation_skill",
                    kv_ok=kv_ok,
                    br=br,
                    get_json=_get_json,
                    set_json=_set_json,
                    ttl=ttl_skill,
                    path_agg=path_agg,
                )
            except Exception as e:
                logger.debug("cdc skill reputation: %s", e)
    log_path = _turn_log_path()
    try:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        MONITOR.inc("cdc_turn_events_total")
    except OSError as e:
        logger.debug("cdc turn log: %s", e)
    return policy


def apply_route_tier_cap(
    tier: str,
    *,
    planned_module: str,
    planned_intent: str,
    persisted: Optional[Dict[str, Any]],
) -> str:
    if not cdc_enabled() or not isinstance(persisted, dict):
        return tier
    pol = persisted.get("cdc_policy") if isinstance(persisted.get("cdc_policy"), dict) else {}
    caps = pol.get("route_tier_caps") if isinstance(pol.get("route_tier_caps"), dict) else {}
    route_key = f"{normalize_module_key(planned_module)}|{(planned_intent or '').strip()}"
    ceiling = caps.get(route_key)
    grim_ceil = pol.get("grim_tier_ceiling")
    from core.task_depth import apply_tier_ceiling

    out = str(tier or "shallow")
    floor = str(pol.get("next_turn_tier_floor") or "").strip()
    if floor in ("nested", "deep"):
        from core.task_depth import max_task_tier

        out = max_task_tier(out, floor)
        # Одноразовый floor: после применения не тащим на последующие ходы.
        if _env_truthy("CDC_CONSUME_NEXT_TIER_FLOOR_ON_USE", True):
            try:
                pol.pop("next_turn_tier_floor", None)
                if isinstance(persisted, dict):
                    persisted["cdc_policy"] = pol
            except Exception as e:
                logger.debug('%s optional failed: %s', 'engine', e, exc_info=True)
    for c in (ceiling, grim_ceil):
        if c:
            out = apply_tier_ceiling(out, str(c))
    if out != tier:
        MONITOR.inc("cdc_tier_cap_applied_total")
    return out


def maybe_apply_planner_penalty(
    decision: Any,
    persisted: Dict[str, Any],
    allowed_modules: Set[str],
) -> Any:
    if not cdc_enabled() or not isinstance(persisted, dict):
        return decision
    pol = persisted.get("cdc_policy") if isinstance(persisted.get("cdc_policy"), dict) else {}
    route_key = f"{normalize_module_key(getattr(decision, 'module_name', '') or '')}|{(getattr(decision, 'intent', '') or '').strip() or 'unknown'}"
    disabled = {str(x) for x in (pol.get("disabled_routes") or []) if str(x).strip()}
    if route_key in disabled:
        dm = pick_dialog_module(allowed_modules)
        if dm and normalize_module_key(dm) != normalize_module_key(getattr(decision, "module_name", "") or ""):
            MONITOR.inc("cdc_module_penalty_total")
            reason = str(getattr(decision, "reason", "") or "")
            return replace(decision, module_name=dm, reason=f"{reason}|cdc_route_disabled")
    if pol.get("grim_force_dialog"):
        dm = pick_dialog_module(allowed_modules)
        if dm and normalize_module_key(dm) != normalize_module_key(getattr(decision, "module_name", "") or ""):
            MONITOR.inc("cdc_module_penalty_total")
            reason = str(getattr(decision, "reason", "") or "")
            return replace(decision, module_name=dm, reason=f"{reason}|grim_force_dialog")
    penalized = pol.get("penalized_modules") or []
    if not isinstance(penalized, list) or not penalized:
        return decision
    pen_set = {normalize_module_key(str(x)) for x in penalized}
    cur = normalize_module_key(getattr(decision, "module_name", "") or "")
    if cur not in pen_set:
        return decision
    dm = pick_dialog_module(allowed_modules)
    if not dm or normalize_module_key(dm) == cur:
        return decision
    MONITOR.inc("cdc_module_penalty_total")
    reason = str(getattr(decision, "reason", "") or "")
    return replace(decision, module_name=dm, reason=f"{reason}|cdc_module_penalty")
