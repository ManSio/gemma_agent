"""Валидаторы ответов для agent_test_runner (probe / Telegram)."""
from __future__ import annotations

import logging

import re
from typing import Any, Callable, Dict, List, Optional

ValidatorFn = Callable[[str, str, Dict[str, Any]], Optional[str]]

_FALLBACK_RE = re.compile(
    r"не удалось сформировать нормальный ответ",
    re.IGNORECASE,
)
_XML_LEAK_RE = re.compile(
    r"(<rule\s+name=|priority\s*=\s*[\"']override|"
    r"системный блок закончился|</rule>|<description>\s*запрещ)",
    re.IGNORECASE,
)
_META_LEAK_RE = re.compile(
    r"(теперь ответь пользователю|tool_call:|available tools\s*\()",
    re.IGNORECASE,
)


logger = logging.getLogger(__name__)

def _no_fallback(reply: str, _user: str, _ctx: Dict[str, Any]) -> Optional[str]:
    if _FALLBACK_RE.search(reply or ""):
        return "fallback_message"
    return None


def _no_leak(reply: str, _user: str, _ctx: Dict[str, Any]) -> Optional[str]:
    s = (reply or "").strip()
    if not s:
        return None
    try:
        from core.text_leak_scan import outbound_has_blocking_leak, primary_blocking_leak_code

        if outbound_has_blocking_leak(s):
            return primary_blocking_leak_code(s) or "leak"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'agent_test_validators', e, exc_info=True)
    if _XML_LEAK_RE.search(s) or _META_LEAK_RE.search(s):
        return "prompt_or_xml_leak"
    return None


def _not_empty(reply: str, _user: str, _ctx: Dict[str, Any]) -> Optional[str]:
    if not (reply or "").strip():
        return "empty_reply"
    return None


def _expect_regex(reply: str, _user: str, ctx: Dict[str, Any]) -> Optional[str]:
    pat = ctx.get("expect_regex")
    if not pat:
        return None
    if not re.search(pat, reply or "", re.IGNORECASE | re.DOTALL):
        return f"regex_miss:{pat[:40]}"
    return None


def _expect_contains(reply: str, _user: str, ctx: Dict[str, Any]) -> Optional[str]:
    need = ctx.get("expect_contains") or []
    low = (reply or "").lower()
    for w in need:
        if str(w).lower() not in low:
            return f"missing:{w}"
    return None


def _no_trivial_ack_on_explain(reply: str, user: str, _ctx: Dict[str, Any]) -> Optional[str]:
    """На «почему/зачем…» нельзя отвечать только ок/да/нет."""
    ut = (user or "").strip()
    if not re.search(
        r"(?i)\b(почему|зачем|отчего|что\s+такое|как\s+работает|объясни)\b",
        ut,
    ):
        return None
    if re.search(r"(?i)(только\s*:\s*|одним\s+словом|say\s+only)", ut):
        return None
    norm = (reply or "").strip().lower().rstrip(".,!?…")
    if norm in ("ок", "ok", "да", "нет", "yes", "no", "ага", "угу", "ладно"):
        return "trivial_ack_on_explain"
    return None


def _expect_not_contains(reply: str, _user: str, ctx: Dict[str, Any]) -> Optional[str]:
    banned = ctx.get("expect_not_contains") or []
    low = (reply or "").lower()
    for w in banned:
        if str(w).lower() in low:
            return f"banned:{w}"
    return None


def _check_preflight_profile(_reply: str, user: str, ctx: Dict[str, Any]) -> Optional[str]:
    """Детерминированный профиль до LLM (инцидент Habr / длинная вставка)."""
    exp = ctx.get("expect_preflight_profile")
    if exp is None:
        return None
    from core.brain.profile_route_guard import preflight_profile

    got = preflight_profile(user)
    if exp == "__none__":
        if got is not None:
            return f"preflight_unexpected:{got}"
        return None
    if str(got) != str(exp):
        return f"preflight:{got}!={exp}"
    return None


def _check_clamp_profile(_reply: str, user: str, ctx: Dict[str, Any]) -> Optional[str]:
    """После ошибочного math_solve роутера — clamp не оставляет math на статье/простыне."""
    from_prof = ctx.get("expect_clamp_from_profile")
    to_prof = ctx.get("expect_clamp_to_profile")
    if not from_prof or not to_prof:
        return None
    from core.brain.profile_route_guard import clamp_profile

    got = clamp_profile(str(from_prof), user, router_confidence=0.98)
    if str(got) != str(to_prof):
        return f"clamp:{got}!={to_prof}"
    return None


def _check_not_operational_diag(_reply: str, user: str, _ctx: Dict[str, Any]) -> Optional[str]:
    """Длинная вставка про RAG не должна считаться «проверь ключ API»."""
    from core.brain.text_helpers import is_bot_operational_diag_question

    if is_bot_operational_diag_question(user):
        return "operational_diag_on_non_admin_text"
    return None


def _check_planner_direct(_reply: str, _user: str, ctx: Dict[str, Any]) -> Optional[str]:
    kind = ctx.get("expect_planner_direct_kind")
    if not kind:
        return None
    from core.brain_own_turn import planner_direct_allowed

    got = planner_direct_allowed(str(kind))
    exp = bool(ctx.get("expect_planner_direct_allowed"))
    if got != exp:
        return f"planner_direct:{kind}:{got}!={exp}"
    return None


def _check_gate_verdict(_reply: str, user: str, ctx: Dict[str, Any]) -> Optional[str]:
    """Gate: ожидаемый verdict для rule_id (allowed/blocked/uncertain)."""
    rule_id = ctx.get("expect_gate_rule_id")
    exp = ctx.get("expect_gate_verdict")
    if not rule_id or not exp:
        return None
    from core.heuristic_context_gate import build_turn_decision_context, shortcut_allowed

    got = shortcut_allowed(str(rule_id), build_turn_decision_context(user)).verdict
    if str(got) != str(exp):
        return f"gate:{rule_id}:{got}!={exp}"
    return None


_REGISTRY: Dict[str, ValidatorFn] = {
    "no_fallback": _no_fallback,
    "no_leak": _no_leak,
    "not_empty": _not_empty,
    "no_trivial_ack_on_explain": _no_trivial_ack_on_explain,
    "expect_regex": _expect_regex,
    "expect_contains": _expect_contains,
    "expect_not_contains": _expect_not_contains,
    "check_preflight_profile": _check_preflight_profile,
    "check_clamp_profile": _check_clamp_profile,
    "check_not_operational_diag": _check_not_operational_diag,
    "check_gate_verdict": _check_gate_verdict,
    "check_planner_direct": _check_planner_direct,
}


def validate_reply(
    reply: str,
    user_text: str,
    case: Dict[str, Any],
) -> List[str]:
    """Список кодов ошибок (пустой = pass)."""
    ctx = dict(case)
    errs: List[str] = []
    names = list(case.get("validators") or ["no_fallback", "no_leak", "not_empty"])
    for name in names:
        fn = _REGISTRY.get(name)
        if not fn:
            errs.append(f"unknown_validator:{name}")
            continue
        code = fn(reply, user_text, ctx)
        if code:
            errs.append(code)
    return errs
