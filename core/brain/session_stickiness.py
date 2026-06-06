"""
KV-Reuse 2.0 — Enhanced session stickiness for KV-cache management.
v3.1.0: forces new session_id on topic-change, reset_dialog_state,
collapse-overflow, noise-sequence. KV-reuse only when topic stable.
v3.2.0: persistent state on disk (survives bot restart), separate
increment_turn() to avoid double-counting.
v3.3.0: profile sessions (.short / .standard / .deep suffix) for
adaptive prompt size without KV-cache invalidation.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple


_STATE: Dict[str, Dict[str, Any]] = {}
_STATE_LOCK = threading.Lock()
_STATE_PATH: Optional[str] = None


# ── Persistent state: save/load to disk ──

def _state_file_path() -> str:
    global _STATE_PATH
    if _STATE_PATH:
        return _STATE_PATH
    raw = (os.getenv("KV_SESSION_STATE_PATH") or "").strip()
    if raw:
        _STATE_PATH = raw
        return raw
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    p = os.path.join(base, "kv_session_state.json")
    _STATE_PATH = p
    return p


def _load_state() -> None:
    """Load _STATE from disk. Called once at module import time."""
    global _STATE
    path = _state_file_path()
    try:
        if os.path.isfile(path):
            raw = open(path, "r", encoding="utf-8").read().strip()
            if not raw:
                return
            data = json.loads(raw)
            if isinstance(data, dict):
                _STATE.update(data)
    except json.JSONDecodeError:
        import logging
        logging.getLogger(__name__).warning(
            "kv session state load: corrupt JSON, reset %s", path
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("kv session state load: %s", exc)


def _save_state() -> None:
    """Atomically save _STATE to disk."""
    try:
        path = _state_file_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_STATE, f, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("kv session state save: %s", exc)


# Load on import
_load_state()


def force_save_state() -> None:
    """Explicit save (e.g. before shutdown)."""
    _save_state()


# ── Helpers ──

def _f(name: str, default: float) -> float:
    import os
    try:
        return float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    import os
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    import os
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _base_key(user_id: Any, group_id: Any) -> str:
    uid = str(user_id or "anon").strip() or "anon"
    gid = str(group_id or "").strip()
    raw = f"u-{uid}_g-{gid}" if gid else f"u-{uid}"
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", raw)[:140]


_CODE_KV_PROFILES = frozenset({"code_generation", "code_debug"})
_SUMMARIZE_KV_PROFILES = frozenset({"summarization", "document_qa", "research"})
_CHAT_KV_PROFILES = frozenset(
    {"standard", "short", "quick_explain", "chitchat", "creative", "roleplay", "news_brief"}
)


def _kv_profile_family(profile: str) -> str:
    p = (profile or "").strip().lower()
    if not p:
        return ""
    if p in _CODE_KV_PROFILES:
        return "code"
    if p in _SUMMARIZE_KV_PROFILES:
        return "summarize"
    if p in _CHAT_KV_PROFILES:
        return "chat"
    return p


def _profile_for_kv_session(
    st: Dict[str, Any],
    profile: str,
    *,
    reset_reason: str,
    explicit_switch: bool,
) -> str:
    """
    Суффикс профиля в session_id. При скачках роутера держим прежний суффикс,
    иначе OpenRouter KV не переиспользуется (hit rate 0%).
    """
    p = (profile or "").strip()
    if not _b("BRAIN_KV_PROFILE_STICKY", True):
        return p
    prev = str(st.get("kv_profile") or st.get("profile") or "").strip()
    if reset_reason or explicit_switch:
        return p
    if prev and p and prev != p:
        fam_prev = _kv_profile_family(prev)
        fam_new = _kv_profile_family(p)
        if fam_prev and fam_new and fam_prev != fam_new:
            return p
        return prev
    return p or prev


def _session_id(base: str, epoch: int, profile: str = "") -> str:
    sid = f"{base}.e{max(0, int(epoch))}"
    p = (profile or "").strip()
    if p:
        sid = f"{sid}.{p}"
    return sid


def _intent_bucket(intent: str) -> str:
    s = str(intent or "").strip().lower()
    if not s:
        return "chat"
    if s in {"reasoning", "logic", "math", "prove", "proof"}:
        return "logic"
    if s in {"debug", "diagnostic", "logs", "health", "connectivity"}:
        return "debug"
    if s in {"admin", "operator", "settings", "command"}:
        return "admin"
    if s in {"chat", "general", "smalltalk", "chitchat"}:
        return "chat"
    return s[:24]


def _bucket_for_intent(intent: str) -> str:
    if not _b("BRAIN_KV_SESSION_PER_INTENT_BUCKET", True):
        return "main"
    return _intent_bucket(intent)


def _session_id_bucketed(base: str, bucket: str, epoch: int, profile: str = "") -> str:
    b = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(bucket or "main"))[:24] or "main"
    if b == "main":
        return _session_id(base, epoch, profile=profile)
    sid = f"{base}.{b}.e{max(0, int(epoch))}"
    p = (profile or "").strip()
    if p:
        sid = f"{sid}.{p}"
    return sid


def _explicit_switch_requested(user_text: str) -> bool:
    s = str(user_text or "").strip().lower()
    if not s:
        return False
    signals = (
        "новая тема",
        "перейдем",
        "перейдём",
        "давай теперь",
        "теперь другое",
        "переключ",
        "сменим тему",
        "анализируй логи",
        "проверим логи",
        "debug",
        "админ",
    )
    return any(x in s for x in signals)


def _kv_reuse_max_age_ms() -> float:
    try:
        from core.token_efficiency import kv_reuse_max_age_ms as _ra
        return float(_ra()) / 1000.0
    except Exception:
        return 600.0


def _kv_reuse_max_turns() -> int:
    try:
        from core.token_efficiency import kv_reuse_max_turns as _rt
        return int(_rt())
    except Exception:
        return 20


def _kv_reuse_enabled() -> bool:
    try:
        from core.token_efficiency import kv_reuse_enabled as _re
        return _re()
    except Exception:
        return False


def _check_dialog_state_reset(user_id: Any, group_id: Any) -> Optional[str]:
    try:
        from core.dialog_state import get_kv_session_epoch
        base = _base_key(user_id, group_id)
        with _STATE_LOCK:
            st = _STATE.get(base) or {}
            last_known_epoch = int(st.get("_last_ds_epoch", 0))
            current_epoch = get_kv_session_epoch(str(user_id), str(group_id or ""))
            if current_epoch != last_known_epoch:
                st["_last_ds_epoch"] = current_epoch
                _STATE[base] = st
                return "dialog_state_reset"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'session_stickiness', e, exc_info=True)
    return None


def force_session_reset(
    *,
    user_id: Any,
    group_id: Any,
    reason: str = "",
    profile: str = "",
) -> str:
    """Force a new KV session. Called by self-healing or orchestrator
    on topic change, collapse overflow, noise sequence, etc."""
    base = _base_key(user_id, group_id)
    with _STATE_LOCK:
        st = _STATE.get(base) or {}
        if not isinstance(st.get("epochs"), dict):
            st["epochs"] = {"main": 0}
        active_bucket = str(st.get("active_bucket") or "main")
        epochs = st.get("epochs") if isinstance(st.get("epochs"), dict) else {"main": 0}
        if active_bucket not in epochs:
            epochs[active_bucket] = 0
        epochs[active_bucket] = int(epochs.get(active_bucket) or 0) + 1
        st["epochs"] = epochs
        st["resets_total"] = int(st.get("resets_total") or 0) + 1
        st["last_reset_reason"] = str(reason or "forced")
        st["kv_reuse_turn_count"] = 0
        st["kv_reuse_start_ts"] = time.time()
        _STATE[base] = st
    _save_state()
    return _session_id_bucketed(base, active_bucket, int(epochs.get(active_bucket) or 0), profile=profile)


def increment_turn(*, user_id: Any, group_id: Any) -> None:
    """
    Increment the turn counter for a user/group.
    Should be called ONCE per successful brain invocation (after prompt sent to LLM).
    When max_turns or max_age is exceeded, a reset_reason is stored (but no epoch bump yet —
    that happens at next resolve_session).
    """
    if not _kv_reuse_enabled():
        return
    now = time.time()
    _kv_max_age_s = _kv_reuse_max_age_ms()
    _kv_max_turns = _kv_reuse_max_turns()
    base = _base_key(user_id, group_id)
    with _STATE_LOCK:
        st = _STATE.get(base) or {}
        kv_turns = int(st.get("kv_reuse_turn_count") or 0)
        kv_start = float(st.get("kv_reuse_start_ts") or 0.0)
        new_turns = kv_turns + 1
        if (new_turns > _kv_max_turns and _kv_max_turns > 0) or (
            kv_start > 0 and _kv_max_age_s > 0 and (now - kv_start) > _kv_max_age_s
        ):
            st["kv_reuse_allowed"] = False
            if new_turns > _kv_max_turns and _kv_max_turns > 0:
                st["kv_turn_breach_reason"] = "max_turns"
            if kv_start > 0 and _kv_max_age_s > 0 and (now - kv_start) > _kv_max_age_s:
                st["kv_turn_breach_reason"] = "max_age"
        st["kv_reuse_turn_count"] = new_turns
        if kv_start <= 0:
            st["kv_reuse_start_ts"] = now
        _STATE[base] = st
    _save_state()


def resolve_session(
    *,
    user_id: Any,
    group_id: Any,
    intent: str,
    prompt_chars: int = 0,
    intent_confidence: Optional[float] = None,
    user_text: str = "",
    profile: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """
    KV-Reuse 3.2: Sticky session with enhanced reset conditions.
    NOTE: Does NOT increment turn counter — call increment_turn() separately
    once per brain invocation.

    Reset conditions:
    - intent changed (bucket switch)
    - inactivity timeout
    - context/prompt too large
    - turn breach flagged by increment_turn()
    - dialog_state reset (topic change, collapse overflow, noise sequence)
    """
    now = time.time()
    base = _base_key(user_id, group_id)
    with _STATE_LOCK:
        st = dict(_STATE.get(base) or {})
    if not isinstance(st.get("epochs"), dict):
        st["epochs"] = {"main": 0}
    st.setdefault("last_intent", "")
    st.setdefault("last_ts", 0.0)
    st.setdefault("last_reset_reason", "")
    st.setdefault("resets_total", 0)
    st.setdefault("active_bucket", "")
    st.setdefault("pending_bucket", "")
    st.setdefault("pending_bucket_count", 0)
    st.setdefault("last_switch_ts", 0.0)
    st.setdefault("kv_reuse_turn_count", 0)
    st.setdefault("kv_reuse_start_ts", 0.0)
    st.setdefault("kv_reuse_last_fallback", False)
    st.setdefault("_last_ds_epoch", 0)
    st.setdefault("kv_reuse_allowed", True)
    st.setdefault("topic_stable_since", 0.0)
    st.setdefault("kv_turn_breach_reason", "")
    st.setdefault("profile", "")
    cur_intent = str(intent or "").strip().lower()
    cur_bucket = _bucket_for_intent(cur_intent)
    timeout_sec = max(300.0, _f("BRAIN_KV_SESSION_TIMEOUT_SEC", 7200.0))
    large_prompt = max(4000, _i("BRAIN_KV_RESET_PROMPT_CHARS", 60000))
    hysteresis_turns = max(1, _i("BRAIN_KV_INTENT_HYSTERESIS_TURNS", 2))
    switch_cooldown = max(0.0, _f("BRAIN_KV_INTENT_SWITCH_COOLDOWN_SEC", 120.0))
    soft_conf = max(0.0, min(1.0, _f("BRAIN_KV_INTENT_SWITCH_CONFIDENCE_SOFT", 0.6)))
    hard_conf = max(0.0, min(1.0, _f("BRAIN_KV_INTENT_SWITCH_CONFIDENCE_HARD", 0.9)))
    if hard_conf < soft_conf:
        hard_conf = soft_conf
    reset_reason = ""
    active_bucket = str(st.get("active_bucket") or "") or cur_bucket or "main"
    pending_bucket = str(st.get("pending_bucket") or "")
    pending_count = int(st.get("pending_bucket_count") or 0)

    conf = None
    try:
        conf = float(intent_confidence) if intent_confidence is not None else None
    except (TypeError, ValueError):
        conf = None
    explicit_switch = _explicit_switch_requested(user_text)

    # KV-Reuse 2.0: check dialog_state reset
    _ds_reset = _check_dialog_state_reset(user_id, group_id)
    if _ds_reset:
        reset_reason = _ds_reset
        st["kv_reuse_allowed"] = False
        st["kv_reuse_turn_count"] = 0
        st["kv_reuse_start_ts"] = now
        st["topic_stable_since"] = now

    # Check turn breach from increment_turn()
    _turn_breach = str(st.get("kv_turn_breach_reason") or "").strip()
    if _turn_breach and not reset_reason:
        reset_reason = f"kv_reuse_{_turn_breach}"
        st["kv_reuse_allowed"] = False
        st["kv_reuse_turn_count"] = 0
        st["kv_reuse_start_ts"] = now
        st["kv_turn_breach_reason"] = ""

    # Check kv_reuse age/turns inline too (as safety net)
    _kvre = _kv_reuse_enabled()
    _kv_max_age_s = _kv_reuse_max_age_ms() if _kvre else 0.0
    if _kvre and not reset_reason and not _turn_breach:
        kv_start = float(st.get("kv_reuse_start_ts") or 0.0)
        kv_turns = int(st.get("kv_reuse_turn_count") or 0)
        if kv_start > 0 and _kv_max_age_s > 0 and (now - kv_start) > _kv_max_age_s:
            reset_reason = "kv_reuse_max_age_inline"
            st["kv_reuse_allowed"] = False

    # Topic stability
    if not reset_reason and st.get("topic_stable_since", 0) > 0:
        if (now - float(st.get("topic_stable_since") or 0)) >= 2.0:
            st["kv_reuse_allowed"] = True
    elif not reset_reason:
        st["topic_stable_since"] = now

    # Intent bucket switching
    if cur_bucket and cur_bucket != active_bucket:
        cooldown_left = switch_cooldown - max(0.0, now - float(st.get("last_switch_ts") or 0.0))
        can_consider_switch = explicit_switch or (conf is not None and conf >= soft_conf)
        force_switch = explicit_switch or (conf is not None and conf >= hard_conf)
        if force_switch:
            active_bucket = cur_bucket
            pending_bucket = ""
            pending_count = 0
            st["last_switch_ts"] = now
            st["last_reset_reason"] = "intent_bucket_switched_forced"
            st["kv_reuse_allowed"] = False
            st["topic_stable_since"] = now
        elif cooldown_left <= 0.0 and can_consider_switch:
            if pending_bucket == cur_bucket:
                pending_count += 1
            else:
                pending_bucket = cur_bucket
                pending_count = 1
            if pending_count >= hysteresis_turns:
                active_bucket = cur_bucket
                pending_bucket = ""
                pending_count = 0
                st["last_switch_ts"] = now
                st["last_reset_reason"] = "intent_bucket_switched"
                st["kv_reuse_allowed"] = False
                st["topic_stable_since"] = now
        else:
            st["last_reset_reason"] = "intent_switch_cooldown"
    else:
        pending_bucket = ""
        pending_count = 0

    epochs = st.get("epochs") if isinstance(st.get("epochs"), dict) else {"main": 0}
    if active_bucket not in epochs:
        epochs[active_bucket] = 0

    if float(st.get("last_ts") or 0.0) > 0 and (now - float(st.get("last_ts") or 0.0)) > timeout_sec:
        reset_reason = "ttl_expired"
    elif int(prompt_chars or 0) >= large_prompt:
        reset_reason = "prompt_too_large"

    if reset_reason:
        epochs[active_bucket] = int(epochs.get(active_bucket) or 0) + 1
        st["resets_total"] = int(st.get("resets_total") or 0) + 1
        st["last_reset_reason"] = reset_reason
        st["kv_reuse_turn_count"] = 0
        st["kv_reuse_start_ts"] = now

    st["epochs"] = epochs
    st["last_intent"] = cur_intent
    st["last_ts"] = now
    st["active_bucket"] = active_bucket
    st["pending_bucket"] = pending_bucket
    st["pending_bucket_count"] = pending_count
    st["profile"] = profile
    kv_profile = _profile_for_kv_session(
        st, profile, reset_reason=reset_reason, explicit_switch=explicit_switch
    )
    if kv_profile:
        st["kv_profile"] = kv_profile
    with _STATE_LOCK:
        _STATE[base] = st
    _save_state()
    sid = _session_id_bucketed(
        base, active_bucket, int(epochs.get(active_bucket) or 0), profile=kv_profile
    )
    router_profile = (profile or "").strip()
    kv_prof = (kv_profile or "").strip()
    dbg = {
        "base": base,
        "session_id": sid,
        "epoch": int(epochs.get(active_bucket) or 0),
        "active_bucket": active_bucket,
        "profile": router_profile,
        "kv_profile": kv_prof,
        "profile_sticky_applied": bool(
            _b("BRAIN_KV_PROFILE_STICKY", True)
            and router_profile
            and kv_prof
            and router_profile != kv_prof
        ),
        "pending_bucket": pending_bucket,
        "pending_bucket_count": pending_count,
        "intent_bucket": cur_bucket,
        "intent_confidence": conf,
        "last_intent": st.get("last_intent") or "",
        "last_reset_reason": st.get("last_reset_reason") or "",
        "resets_total": int(st.get("resets_total") or 0),
        "last_ts_unix": int(st.get("last_ts") or 0),
        "kv_reuse_enabled": _kvre,
        "kv_reuse_turn_count": int(st.get("kv_reuse_turn_count") or 0),
        "kv_reuse_max_turns": _kv_reuse_max_turns(),
        "kv_reuse_max_age_ms": int(_kv_max_age_s * 1000),
        "kv_reuse_allowed": bool(st.get("kv_reuse_allowed", True)),
        "topic_stable_since": float(st.get("topic_stable_since") or 0),
        "kv_session_source": "disk",
    }
    return sid, dbg


def debug_snapshot(*, user_id: Any, group_id: Any) -> Dict[str, Any]:
    base = _base_key(user_id, group_id)
    with _STATE_LOCK:
        st = dict(_STATE.get(base) or {})
    epochs = st.get("epochs") if isinstance(st.get("epochs"), dict) else {"main": int(st.get("epoch") or 0)}
    active_bucket = str(st.get("active_bucket") or "main")
    profile = str(st.get("profile") or "").strip()
    sid = _session_id_bucketed(base, active_bucket, int(epochs.get(active_bucket) or 0), profile=profile)
    return {
        "base": base,
        "session_id": sid,
        "profile": profile,
        "epoch": int(epochs.get(active_bucket) or 0),
        "active_bucket": active_bucket,
        "pending_bucket": str(st.get("pending_bucket") or ""),
        "pending_bucket_count": int(st.get("pending_bucket_count") or 0),
        "epochs_by_bucket": epochs,
        "last_intent": str(st.get("last_intent") or ""),
        "last_reset_reason": str(st.get("last_reset_reason") or ""),
        "resets_total": int(st.get("resets_total") or 0),
        "last_ts_unix": int(st.get("last_ts") or 0),
        "kv_reuse_allowed": bool(st.get("kv_reuse_allowed", True)),
        "kv_reuse_turn_count": int(st.get("kv_reuse_turn_count") or 0),
        "kv_session_state_path": _state_file_path(),
    }
