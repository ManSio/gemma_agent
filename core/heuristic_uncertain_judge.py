"""Дешёвый LLM-судья для verdict=uncertain у heuristic gate (B3)."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from core.heuristic_context_gate import GateResult, TurnDecisionContext

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def uncertain_llm_enabled() -> bool:
    raw = os.getenv("HEURISTIC_UNCERTAIN_LLM_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _min_confidence() -> float:
    try:
        return max(0.0, min(1.0, float((os.getenv("HEURISTIC_UNCERTAIN_MIN_CONFIDENCE") or "0.55").strip())))
    except ValueError:
        return 0.55


def _parse_json_obj(raw: str) -> Dict[str, Any]:
    s = (raw or "").strip()
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(s[i : j + 1])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def _dialogue_tail(ctx: TurnDecisionContext, limit: int = 4) -> str:
    lines = []
    for row in (ctx.recent_dialogue or [])[-limit:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "?").strip()
        text = str(row.get("text") or "").strip().replace("\n", " ")
        if len(text) > 180:
            text = text[:177] + "…"
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


async def judge_shortcut_uncertain(
    rule_id: str,
    ctx: TurnDecisionContext,
    *,
    user_text: str = "",
) -> GateResult:
    """
    Разрешить или заблокировать shortcut в серой зоне.
    При ошибке LLM — blocked (консервативно).
    """
    rid = str(rule_id or "").strip()
    if not rid or not uncertain_llm_enabled():
        return GateResult(verdict="blocked", rule_id=rid, reason="uncertain_no_judge")

    try:
        from core.heuristic_shortcuts_registry import get_rule

        rule = get_rule(rid) or {}
    except Exception:
        rule = {}

    desc = str(rule.get("description") or rid)
    tier = str(rule.get("tier") or "")
    text = (user_text or ctx.user_text or "").strip()
    try:
        max_ch = max(200, min(1200, int((os.getenv("HEURISTIC_UNCERTAIN_MAX_CHARS") or "600").strip())))
    except ValueError:
        max_ch = 600
    excerpt = text[:max_ch]
    tail = _dialogue_tail(ctx)

    model = (
        (os.getenv("OPENROUTER_MODEL_HEURISTIC_UNCERTAIN") or "").strip()
        or (os.getenv("ROUTER_LLM_MODEL") or "").strip()
        or None
    )
    sys = (
        "Ты судья: можно ли применить shortcut-правило к последней реплике пользователя. "
        "Ответь ТОЛЬКО JSON: {\"allow\": true|false, \"confidence\": 0..1, \"reason\": \"кратко\"}. "
        "allow=true только если реплика — явная команда под правило, а не случайное слово в длинном тексте/истории."
    )
    user_msg = (
        f"rule_id: {rid}\n"
        f"tier: {tier}\n"
        f"description: {desc}\n"
        f"topic: {ctx.topic_current or '—'}\n"
        f"prose_score: {ctx.prose_score:.2f}\n"
        f"message:\n{excerpt}"
    )
    if tail:
        user_msg += f"\n\ndialogue_tail:\n{tail}"

    try:
        from core.openrouter_provider import get_openrouter_provider

        prov = get_openrouter_provider()
        out = await prov.generate(
            prompt=user_msg,
            model=model,
            system_prompt=sys,
            max_tokens=96,
            temperature=0.05,
            telemetry_tag="heuristic_uncertain",
        )
    except Exception as e:
        logger.debug("heuristic_uncertain llm: %s", e)
        return GateResult(verdict="blocked", rule_id=rid, reason="uncertain_llm_error")

    if out.get("error"):
        return GateResult(verdict="blocked", rule_id=rid, reason="uncertain_llm_error")

    obj = _parse_json_obj(str(out.get("content") or ""))
    allow = obj.get("allow")
    if isinstance(allow, str):
        allow = allow.strip().lower() in {"1", "true", "yes", "on"}
    try:
        conf = float(obj.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    reason = str(obj.get("reason") or "").strip()[:120]

    if allow and conf >= _min_confidence():
        return GateResult(verdict="allowed", rule_id=rid, reason=f"uncertain_llm_ok:{reason or 'ok'}")
    return GateResult(
        verdict="blocked",
        rule_id=rid,
        reason=f"uncertain_llm_deny:{reason or 'low_conf'}",
    )
