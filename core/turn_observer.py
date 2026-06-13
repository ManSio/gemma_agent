"""
Единый журнал хода: архив + CDC + feedback + эвристики issues.

Файл: data/runtime/turns.jsonl (GEMMA_TURNS_LOG_PATH).
Подписка на event bus turn.outcome (см. install_turn_observer).

Полный текст диалога — в behavior_store, не здесь; см. docs/CONVERSATION_LOGS_MAP_RU.md
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.experience_memory import fingerprint
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_INSTALLED = False

_LEAK_RE = re.compile(
    r"(?i)(ArithmeticTool|Style:|Tools:|external_hint|ephemeral_lessons|"
    r"route_risk_hint|planner_reason|brain_first)"
)


def turn_observer_enabled() -> bool:
    return effective_bool("TURN_OBSERVER_ENABLED", default=True)


def _project_root() -> Path:
    for key in ("GEMMA_PROJECT_ROOT", "PROJECT_ROOT"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            return Path(raw).resolve()
    return Path(__file__).resolve().parent.parent


def log_path() -> Path:
    raw = (os.getenv("GEMMA_TURNS_LOG_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = _project_root() / p
        return p.resolve()
    return (_project_root() / "data" / "runtime" / "turns.jsonl").resolve()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_tail(path: Path, max_lines: int) -> None:
    if max_lines <= 0 or not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    try:
        path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except OSError as e:
        logger.debug("turn_observer trim: %s", e)


def detect_issues(
    *,
    outcome: str,
    user_feedback_negative: bool,
    user_feedback_positive: bool,
    assistant_excerpt: str,
    detail: str,
    delivery_normalize_status: str = "",
    short_turn_kind: str = "",
    outbound_thread_guard_issues: Optional[List[str]] = None,
) -> List[str]:
    issues: List[str] = []
    o = (outcome or "").strip().lower()
    if user_feedback_negative:
        issues.append("user_feedback_negative")
    if o in ("failure", "error"):
        issues.append(f"outcome_{o}")
    if o == "fallback":
        issues.append("outcome_fallback")
    if o == "clarify":
        issues.append("outcome_clarify")
    at = (assistant_excerpt or "").strip()
    detail_low = (detail or "").strip().lower()
    if "reply_echo" in detail_low or "topic_drift" in detail_low:
        issues.append("product_behavior")
    if "product_behavior:" in detail_low:
        issues.append("product_behavior")
    if at and _LEAK_RE.search(at):
        issues.append("prompt_leak_suspect")
    if at and len(at) < 12 and o == "ok":
        issues.append("short_reply")
    d = (detail or "").strip().lower()
    if d and any(x in d for x in ("format_", "noise_misread", "meta_tutor")):
        issues.append("semantic_failure")
    try:
        from core.brain.user_facing_contract import detect_delivery_issues

        for tag in detect_delivery_issues(
            assistant_excerpt,
            detail=detail,
            normalize_status=delivery_normalize_status,
        ):
            if tag not in issues:
                issues.append(tag)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'turn_observer', e, exc_info=True)
    if delivery_normalize_status and delivery_normalize_status != "ok":
        tag = f"delivery_normalize_{delivery_normalize_status}"
        if tag not in issues:
            issues.append(tag)
    if isinstance(outbound_thread_guard_issues, list):
        for tag in outbound_thread_guard_issues:
            key = str(tag or "").strip()[:48]
            if key and key not in issues:
                issues.append(key)
    return issues


def append_turn_record(row: Dict[str, Any]) -> None:
    if not turn_observer_enabled():
        return
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except OSError as e:
        logger.debug("turn_observer append: %s", e)
        return
    try:
        max_lines = int((os.getenv("TURN_OBSERVER_MAX_LINES") or "12000").strip() or "12000")
        if path.is_file() and path.stat().st_size > 2_500_000:
            _trim_tail(path, max_lines)
    except (OSError, ValueError):
        pass


def record_from_turn_outcome(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    user_excerpt = str(payload.get("user_excerpt") or "")[:240]
    assistant_excerpt = str(payload.get("assistant_excerpt") or "")[:480]
    outcome = str(payload.get("outcome") or "")
    detail = str(payload.get("detail") or "")[:200]
    neg = bool(payload.get("user_feedback_negative"))
    pos = bool(payload.get("user_feedback_positive"))
    _otg_in = payload.get("outbound_thread_guard_issues")
    _otg_list = [str(x)[:48] for x in _otg_in[:8]] if isinstance(_otg_in, list) else None
    issues = detect_issues(
        outcome=outcome,
        user_feedback_negative=neg,
        user_feedback_positive=pos,
        assistant_excerpt=assistant_excerpt,
        detail=detail,
        delivery_normalize_status=str(payload.get("delivery_normalize_status") or ""),
        short_turn_kind=str(payload.get("short_turn_kind") or ""),
        outbound_thread_guard_issues=_otg_list,
    )
    fp = str(payload.get("fp") or "")
    if not fp and user_excerpt:
        fp = fingerprint(user_excerpt)
    _tid = str(payload.get("trace_id") or "").strip()
    row = {
        "ts": _now_iso(),
        "user_id": str(payload.get("user_id") or ""),
        "group_id": payload.get("group_id"),
        "fp": fp,
        "intent": str(payload.get("intent") or ""),
        "module": str(payload.get("module") or ""),
        "skill": payload.get("skill"),
        "profile": str(payload.get("profile") or ""),
        "dialogue_lane": str(payload.get("dialogue_lane") or ""),
        "outcome": outcome,
        "task_tier": str(payload.get("task_tier") or ""),
        "latency_ms": payload.get("latency_ms"),
        "prompt_tokens_est": payload.get("prompt_tokens_est"),
        "brain_recent_limit": payload.get("brain_recent_limit"),
        "completion_tokens": payload.get("completion_tokens"),
        "ok": bool(payload.get("ok")),
        "user_excerpt": user_excerpt,
        "assistant_excerpt": assistant_excerpt,
        "issues": issues,
        "detail": detail,
    }
    if _tid:
        row["trace_id"] = _tid[:64]
    pb = str(payload.get("planner_bypass") or "").strip()
    if pb:
        row["planner_bypass"] = pb
    pr = str(payload.get("planner_reason") or "").strip()
    if pr:
        row["planner_reason"] = pr[:240]
    lt = str(payload.get("last_tool") or "").strip()
    if lt:
        row["last_tool"] = lt[:80]
    if payload.get("last_tool_ok") is not None:
        row["last_tool_ok"] = bool(payload.get("last_tool_ok"))
    if payload.get("delivery_ok") is not None:
        row["delivery_ok"] = bool(payload.get("delivery_ok"))
    _pll = str(payload.get("pre_llm_lane") or "").strip()
    if _pll:
        row["pre_llm_lane"] = _pll[:64]
    _drh = str(payload.get("direct_reply_head") or "").strip()
    if _drh:
        row["direct_reply_head"] = _drh[:80]
    _ats = str(payload.get("article_thread_subject") or "").strip()
    if _ats:
        row["article_thread_subject"] = _ats[:120]
    sc = payload.get("scenario_hits")
    if isinstance(sc, list) and sc:
        row["scenario_hits"] = sc[:12]
    ra = payload.get("router_route_audit")
    if isinstance(ra, dict) and ra:
        row["router_source"] = str(ra.get("router_source") or "")
        row["router_profile"] = str(ra.get("router_profile") or "")
        if ra.get("preflight"):
            row["route_preflight"] = ra.get("preflight")
        if ra.get("continuation"):
            row["route_continuation"] = ra.get("continuation")
        if ra.get("final_profile") and ra.get("final_profile") != row.get("profile"):
            row["profile_final"] = ra.get("final_profile")
        sa = ra.get("semantic_audit")
        if isinstance(sa, dict) and sa.get("mismatch"):
            row["route_semantic_mismatch"] = True
            row["classifier_profile"] = str(sa.get("classifier_profile") or "")
        hg = ra.get("heuristic_gate")
        if isinstance(hg, list) and hg:
            row["heuristic_gate"] = hg[-6:]
            last_hg = hg[-1] if isinstance(hg[-1], dict) else {}
            if last_hg.get("shortcut_rule_id"):
                row["shortcut_rule_id"] = str(last_hg.get("shortcut_rule_id"))
            if last_hg.get("gate_verdict"):
                row["gate_verdict"] = str(last_hg.get("gate_verdict"))
            if last_hg.get("gate_block_reason"):
                row["gate_block_reason"] = str(last_hg.get("gate_block_reason"))
            if last_hg.get("topic_current"):
                row["topic_current"] = str(last_hg.get("topic_current"))
        disc = ra.get("discourse")
        if isinstance(disc, dict) and disc:
            if disc.get("action"):
                row["discourse_action"] = str(disc.get("action"))
            if disc.get("continuation") is not None:
                row["discourse_continuation"] = bool(disc.get("continuation"))
            if disc.get("reason"):
                row["discourse_reason"] = str(disc.get("reason"))[:120]
            if disc.get("inherit_intent"):
                row["discourse_inherit_intent"] = str(disc.get("inherit_intent"))
            if disc.get("inherit_profile"):
                row["discourse_inherit_profile"] = str(disc.get("inherit_profile"))
            if disc.get("judge_source"):
                row["discourse_judge_source"] = str(disc.get("judge_source"))
    _da = payload.get("discourse_audit")
    if isinstance(_da, dict) and _da and not row.get("discourse_action"):
        if _da.get("action"):
            row["discourse_action"] = str(_da.get("action"))
        if _da.get("reason"):
            row["discourse_reason"] = str(_da.get("reason"))[:120]
    _tsa = payload.get("turn_state_audit")
    if isinstance(_tsa, dict) and _tsa:
        if _tsa.get("slot_cleared") is not None:
            row["slot_cleared"] = bool(_tsa.get("slot_cleared"))
        if _tsa.get("expects_correction") is not None:
            row["expects_correction"] = bool(_tsa.get("expects_correction"))
        if _tsa.get("prior_outcome"):
            row["prior_outcome"] = str(_tsa.get("prior_outcome"))[:32]
        if _tsa.get("short_turn_kind"):
            row["short_turn_kind"] = str(_tsa.get("short_turn_kind"))[:24]
        if _tsa.get("speech_act"):
            row["speech_act"] = str(_tsa.get("speech_act"))[:24]
        if _tsa.get("referent"):
            row["referent"] = str(_tsa.get("referent"))[:16]
        if _tsa.get("meaning_source"):
            row["meaning_source"] = str(_tsa.get("meaning_source"))[:16]
    _tma = payload.get("turn_meaning_audit")
    if isinstance(_tma, dict) and _tma:
        for key in ("speech_act", "referent", "meaning_source", "thread_action"):
            if _tma.get(key) and not row.get(key):
                row[key] = str(_tma.get(key))[:24]
    tt = payload.get("topic_tracking")
    if isinstance(tt, dict):
        cur = str(tt.get("current") or "").strip()
        if cur and not row.get("topic_current"):
            row["topic_current"] = cur[:120]
        sn = str(tt.get("snippet") or "").strip()
        if sn:
            row["topic_snippet"] = sn[:160]
    kv = payload.get("kv_session_debug")
    if isinstance(kv, dict) and kv:
        if kv.get("session_id"):
            row["kv_session_id"] = str(kv.get("session_id"))
        if kv.get("epoch") is not None:
            row["kv_epoch"] = int(kv.get("epoch") or 0)
        if kv.get("last_reset_reason"):
            row["kv_reset_reason"] = str(kv.get("last_reset_reason"))
        if kv.get("profile_sticky_applied"):
            row["kv_profile_sticky"] = True
        if kv.get("kv_profile"):
            row["kv_profile"] = str(kv.get("kv_profile"))
    _dns = str(payload.get("delivery_normalize_status") or "").strip()
    if _dns:
        row["delivery_normalize_status"] = _dns
    _stk = str(payload.get("short_turn_kind") or "").strip()
    if _stk:
        row["short_turn_kind"] = _stk
    _sms = payload.get("stage_ms")
    if isinstance(_sms, dict) and _sms:
        row["stage_ms"] = {k: int(v) for k, v in _sms.items() if v is not None}
    _dt = payload.get("decision_trace")
    if isinstance(_dt, dict) and _dt:
        row["decision_trace"] = _dt
    _cmp = payload.get("compaction")
    if isinstance(_cmp, dict) and _cmp:
        row["compaction"] = _cmp
    _dsk = str(payload.get("dialogue_slot_kind") or "").strip()
    if _dsk:
        row["dialogue_slot_kind"] = _dsk[:64]
    _pht = payload.get("policy_hint_tags")
    if isinstance(_pht, list) and _pht:
        row["policy_hint_tags"] = [str(x)[:32] for x in _pht[:12]]
    _psk = payload.get("policy_slot_keys")
    if isinstance(_psk, list) and _psk:
        row["policy_slot_keys"] = [str(x)[:32] for x in _psk[:12]]
    if payload.get("correction_pending") is True:
        row["correction_pending"] = True
    _lfa = payload.get("last_feedback_applied")
    if isinstance(_lfa, list) and _lfa:
        row["last_feedback_applied"] = [str(x)[:40] for x in _lfa[:8]]
    if _otg_list:
        row["outbound_thread_guard_issues"] = _otg_list
    append_turn_record(row)


def _on_turn_outcome(payload: Dict[str, Any]) -> None:
    try:
        record_from_turn_outcome(payload)
    except Exception as e:
        logger.debug("turn_observer: %s", e)


def enrich_turn_record_by_trace_id(trace_id: str, patch: Dict[str, Any]) -> bool:
    """Patch the latest turn row matching trace_id (e.g. after pre_send guard)."""
    tid = (trace_id or "").strip()
    if not tid or not isinstance(patch, dict) or not patch:
        return False
    path = log_path()
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") in ("scenario", "pre_send"):
            continue
        if str(row.get("trace_id") or "") != tid:
            continue
        for key, val in patch.items():
            if val is None:
                continue
            row[key] = val
        otg = patch.get("outbound_thread_guard_issues")
        if isinstance(otg, list) and otg:
            iss = row.get("issues") if isinstance(row.get("issues"), list) else []
            for tag in otg:
                key = str(tag or "").strip()[:48]
                if key and key not in iss:
                    iss.append(key)
            row["issues"] = iss
        lines[i] = json.dumps(row, ensure_ascii=False, default=str)
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as e:
            logger.debug("turn_observer enrich: %s", e)
            return False
        return True
    return False


def record_from_turn_pre_send(payload: Dict[str, Any]) -> None:
    """Merge pre_send guard telemetry into the matching turn row."""
    if not isinstance(payload, dict):
        return
    tid = str(payload.get("trace_id") or "").strip()
    if not tid:
        return
    patch: Dict[str, Any] = {}
    otg = payload.get("outbound_thread_guard_issues")
    if isinstance(otg, list) and otg:
        patch["outbound_thread_guard_issues"] = [str(x)[:48] for x in otg[:8]]
    sps = payload.get("scenario_pre_send")
    if isinstance(sps, list) and sps:
        patch["scenario_pre_send"] = sps[:12]
    if not patch:
        return
    if enrich_turn_record_by_trace_id(tid, patch):
        return
    append_turn_record(
        {
            "ts": _now_iso(),
            "type": "pre_send",
            "trace_id": tid[:64],
            "user_id": str(payload.get("user_id") or ""),
            **patch,
        }
    )


def _on_turn_pre_send(payload: Dict[str, Any]) -> None:
    try:
        record_from_turn_pre_send(payload)
    except Exception as e:
        logger.debug("turn_observer pre_send: %s", e)


def _on_turn_scenario(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    hits = payload.get("scenario_hits")
    if not isinstance(hits, list) or not hits:
        return
    append_turn_record(
        {
            "ts": _now_iso(),
            "type": "scenario",
            "user_id": str(payload.get("user_id") or ""),
            "scenario_hits": hits[:12],
        }
    )


def read_recent_turns(
    *,
    limit: int = 20,
    issues_only: bool = False,
    user_id: str = "",
) -> List[Dict[str, Any]]:
    """Последние записи turns.jsonl (новые в конце списка)."""
    path = log_path()
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") in ("scenario", "pre_send"):
            continue
        if user_id and str(row.get("user_id") or "") != str(user_id):
            continue
        if issues_only:
            iss = row.get("issues")
            if not isinstance(iss, list) or not iss:
                continue
        rows.append(row)
    cap = max(1, min(int(limit), 200))
    return rows[-cap:]


def format_turns_admin_html(
    rows: List[Dict[str, Any]],
    *,
    title: str = "Последние ходы (turns.jsonl)",
) -> str:
    from core.telegram_ui import esc

    if not rows:
        return f"<b>{esc(title)}</b>\n\n<i>Записей нет. Проверьте TURN_OBSERVER_ENABLED и GEMMA_TURNS_LOG_PATH.</i>"
    lines = [f"<b>{esc(title)}</b>", ""]
    for row in reversed(rows):
        ts = esc(str(row.get("ts") or "")[:19])
        oc = esc(str(row.get("outcome") or "?"))
        prof = esc(str(row.get("profile") or ""))
        lane = esc(str(row.get("dialogue_lane") or ""))
        lat = row.get("latency_ms")
        pt = row.get("prompt_tokens_est")
        issues = row.get("issues") if isinstance(row.get("issues"), list) else []
        iss_s = ",".join(issues[:4]) if issues else "—"
        ue = esc(str(row.get("user_excerpt") or "")[:60])
        ae = esc(str(row.get("assistant_excerpt") or "")[:80])
        mod = esc(str(row.get("module") or "")[:24])
        intent = esc(str(row.get("intent") or "")[:16])
        lat_ms = int(lat) if lat is not None else 0
        lane_bit = f" <code>{lane}</code>" if lane else ""
        tok_bit = f" pt≈{int(pt)}" if pt else ""
        mod_bit = f" <i>{mod}</i>" if mod else ""
        topic = esc(str(row.get("topic_current") or row.get("topic_snippet") or "")[:48])
        gate = esc(str(row.get("gate_verdict") or ""))
        rule = esc(str(row.get("shortcut_rule_id") or ""))
        gate_bit = ""
        if gate or rule:
            gate_bit = f"\n  gate: <code>{gate or '—'}</code>"
            if rule:
                gate_bit += f" rule=<code>{rule}</code>"
            br = esc(str(row.get("gate_block_reason") or "")[:40])
            if br:
                gate_bit += f" · {br}"
        topic_bit = f"\n  topic: <code>{topic}</code>" if topic else ""
        lines.append(
            f"• <code>{ts}</code> <b>{oc}</b> <code>{prof}</code>{mod_bit}{lane_bit} "
            f"<b>{lat_ms}ms</b>{tok_bit}"
            f"\n  intent: <code>{intent or '—'}</code> · issues: <code>{esc(iss_s)}</code>"
            f"{topic_bit}{gate_bit}"
            f"\n  U: {ue}"
            f"\n  A: {ae}"
        )
    return "\n".join(lines)


def install_turn_observer() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from core.event_bus import bus

    bus.subscribe("turn.outcome", _on_turn_outcome)
    bus.subscribe("turn.scenario", _on_turn_scenario)
    bus.subscribe("turn.pre_send", _on_turn_pre_send)
    _INSTALLED = True
    logger.info("[turn_observer] installed → %s", log_path())
