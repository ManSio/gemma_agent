"""
Context gate для shortcut-правил: не срабатывать по одному слову без темы диалога.

Источник правил: config/heuristic_shortcuts.json (+ optional .local.json).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence

logger = logging.getLogger(__name__)

GateVerdict = Literal["allowed", "blocked", "uncertain"]


def gate_enabled() -> bool:
    raw = os.getenv("HEURISTIC_GATE_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _prose_max_chars() -> int:
    try:
        v = int((os.getenv("HEURISTIC_PROSE_MAX_CHARS") or "140").strip())
    except ValueError:
        v = 140
    return max(60, min(600, v))


def _uncertain_min_chars() -> int:
    try:
        v = int((os.getenv("HEURISTIC_UNCERTAIN_MIN_CHARS") or "35").strip())
    except ValueError:
        v = 35
    return max(20, min(200, v))


@dataclass
class TurnDecisionContext:
    user_text: str = ""
    recent_dialogue: List[Dict[str, Any]] = field(default_factory=list)
    dialogue_state: Dict[str, Any] = field(default_factory=dict)
    topic_current: str = ""
    last_brain_profile: str = ""
    last_intent: str = ""
    last_assistant_text: str = ""
    has_attachment: bool = False
    has_telegram_location: bool = False
    has_saved_location: bool = False
    pending_correction: bool = False
    prose_score: float = 0.0
    text_len: int = 0
    fast_path_candidate: bool = False
    ultra_short_text: bool = False
    persisted: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    verdict: GateVerdict
    rule_id: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"


def gate_audit_fields(result: GateResult, *, topic_current: str = "") -> Dict[str, Any]:
    return {
        "shortcut_rule_id": result.rule_id,
        "gate_verdict": result.verdict,
        "gate_block_reason": result.reason if result.verdict != "allowed" else "",
        "topic_current": (topic_current or "").strip() or None,
    }


def topic_anchor_in_hint_enabled() -> bool:
    raw = os.getenv("BRAIN_TOPIC_ANCHOR_IN_HINT", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_topic_gate_hint(topic_tracking: Any) -> str:
    """Одна строка в external_hint — якорь темы (B7)."""
    if not topic_anchor_in_hint_enabled():
        return ""
    if not isinstance(topic_tracking, dict):
        return ""
    cur = str(topic_tracking.get("current") or "").strip()
    if len(cur) < 4:
        return ""
    snippet = str(topic_tracking.get("snippet") or "").strip()
    if snippet and snippet != cur and len(snippet) > len(cur):
        return (
            f"Тема диалога (сохраняй связность, не уходи в другой домен без явного запроса): "
            f"{cur[:100]}"
        )
    return (
        f"Тема диалога (сохраняй связность, не уходи в другой домен без явного запроса): "
        f"{cur[:120]}"
    )


def append_gate_audit(
    planner_context: Optional[Dict[str, Any]],
    result: GateResult,
    *,
    topic_current: str = "",
) -> None:
    """Накопить решения gate для turns.jsonl (router_route_audit.heuristic_gate)."""
    if not isinstance(planner_context, dict):
        return
    audits = planner_context.setdefault("_heuristic_gate_audit", [])
    if not isinstance(audits, list):
        planner_context["_heuristic_gate_audit"] = audits = []
    audits.append(gate_audit_fields(result, topic_current=topic_current))
    if len(audits) > 12:
        del audits[:-12]


def _last_assistant_from_dialogue(rows: Sequence[Any]) -> str:
    if not rows:
        return ""
    for row in reversed(list(rows)):
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role in ("assistant", "bot", "gemma"):
            return str(row.get("text") or row.get("content") or "").strip()
    return ""


def _compute_prose_score(text: str) -> float:
    raw = (text or "").strip()
    if not raw:
        return 0.0
    score = 0.0
    n = len(raw)
    if n >= _prose_max_chars():
        score += 0.45
    if raw.count("\n") >= 2:
        score += 0.2
    low = raw.lower()
    if "http://" in low or "https://" in low or "t.me/" in low:
        score += 0.25
    try:
        from core.intent_heuristics import prose_narrative_disfavors_calculator

        if prose_narrative_disfavors_calculator(raw):
            score += 0.35
    except Exception as e:
        logger.debug("gate prose narrative: %s", e)
    if n > 280 and any(w in low for w in ("напоминан", "рядом", "ошибк", "напиши")):
        score += 0.15
    return min(1.0, score)


def build_turn_decision_context(
    user_text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    persisted: Optional[Dict[str, Any]] = None,
    planner_context: Optional[Dict[str, Any]] = None,
    fast_path_candidate: bool = False,
    ultra_short_text: bool = False,
) -> TurnDecisionContext:
    txt = (user_text or "").strip()
    meta = meta if isinstance(meta, dict) else {}
    persisted = persisted if isinstance(persisted, dict) else {}
    planner_context = planner_context if isinstance(planner_context, dict) else {}

    rd = planner_context.get("recent_dialogue") or planner_context.get("recent_messages")
    if not isinstance(rd, list):
        rd = []
    if not rd and isinstance(persisted.get("recent_messages"), list):
        rd = persisted["recent_messages"]

    ds = persisted.get("dialogue_state")
    if not isinstance(ds, dict):
        ds = planner_context.get("dialogue_state") if isinstance(planner_context.get("dialogue_state"), dict) else {}

    tt = planner_context.get("topic_tracking")
    if not isinstance(tt, dict):
        tt = persisted.get("topic_tracking") if isinstance(persisted.get("topic_tracking"), dict) else {}

    topic = str(tt.get("current") or "").strip() if isinstance(tt, dict) else ""

    rp = persisted.get("routing_prefs")
    pending_corr = False
    if isinstance(rp, dict) and isinstance(rp.get("pending_correction"), dict):
        pending_corr = True

    last_loc = False
    if isinstance(ds, dict) and isinstance(ds.get("last_telegram_location"), dict):
        try:
            float(ds["last_telegram_location"].get("latitude"))
            float(ds["last_telegram_location"].get("longitude"))
            last_loc = True
        except (TypeError, ValueError, KeyError):
            last_loc = False

    has_tl = isinstance(meta.get("telegram_location"), dict)
    last_asst = _last_assistant_from_dialogue(rd)
    if not last_asst and isinstance(ds, dict):
        last_asst = str(ds.get("last_assistant_text") or ds.get("last_response") or "").strip()

    has_tl = has_tl or bool(
        isinstance(planner_context.get("telegram_location"), dict)
        if isinstance(planner_context, dict)
        else False
    )

    return TurnDecisionContext(
        user_text=txt,
        recent_dialogue=[r for r in rd if isinstance(r, dict)],
        dialogue_state=dict(ds) if isinstance(ds, dict) else {},
        topic_current=topic,
        last_brain_profile=str(
            ds.get("last_brain_profile") or ds.get("brain_profile") or planner_context.get("last_brain_profile") or ""
        ).strip().lower(),
        last_intent=str(ds.get("last_intent") or planner_context.get("last_intent") or "general").strip().lower(),
        last_assistant_text=last_asst,
        has_attachment=bool(meta.get("has_attachment") or meta.get("file_type")),
        has_telegram_location=has_tl,
        has_saved_location=last_loc,
        pending_correction=pending_corr,
        prose_score=_compute_prose_score(txt),
        text_len=len(txt),
        fast_path_candidate=fast_path_candidate,
        ultra_short_text=ultra_short_text,
        persisted=dict(persisted) if isinstance(persisted, dict) else {},
    )


def _domain_matches(rule_domain: str, ctx: TurnDecisionContext) -> bool:
    dom = (rule_domain or "").strip().lower()
    topic = (ctx.topic_current or "").strip().lower()
    if not dom or not topic or len(topic) < 4:
        return True
    if dom == topic:
        return True
    if dom == "geo" and topic in ("geo", "location", "maps", "weather"):
        return True
    if dom == "reminder" and topic in ("reminder", "schedule"):
        return True
    if dom == "tools" and topic in ("tools", "document", "code"):
        return True
    conflicting = (
        (dom == "geo" and topic in ("medical", "dental", "health", "legal", "finance")),
        (dom == "reminder" and topic in ("news", "article", "research")),
    )
    if any(conflicting):
        return False
    return True


def _negative_pattern_hits(text: str, rule: Dict[str, Any]) -> Optional[str]:
    pats = rule.get("negative_patterns") or []
    if not isinstance(pats, list) or not pats:
        return None
    low = (text or "").lower()
    for p in pats:
        s = str(p or "").strip().lower()
        if len(s) >= 3 and s in low:
            return "negative_pattern"
    return None


def _check_block_flag(flag: str, ctx: TurnDecisionContext, rule: Dict[str, Any]) -> Optional[str]:
    if flag == "prose_over_chars":
        if ctx.text_len > _prose_max_chars() or ctx.prose_score >= 0.45:
            return "prose_over_chars"
    elif flag == "relational_ryadom_without_explicit_geo":
        try:
            from core.geo_nearby_reply import is_explicit_nearby_request, is_relational_ryadom

            if is_relational_ryadom(ctx.user_text) and not is_explicit_nearby_request(ctx.user_text):
                return "relational_ryadom_without_explicit_geo"
        except Exception as e:
            logger.debug("gate relational_ryadom: %s", e)
    elif flag == "reminder_prose_skip":
        try:
            from core.reminder_nl import _REMINDER_PROSE_SKIP_RE

            if ctx.text_len > 280 and _REMINDER_PROSE_SKIP_RE.search(ctx.user_text):
                return "reminder_prose_skip"
        except Exception as e:
            logger.debug("gate reminder_prose_skip: %s", e)
    elif flag == "pending_correction":
        if ctx.pending_correction:
            # Старая коррекция от 👎 не должна гонять «привет» через тяжёлый brain.
            rid = str(rule.get("id") or "").strip()
            if rid == "chitchat_fast_eligible":
                try:
                    from core.prompt_routing import is_pure_chitchat_private

                    if is_pure_chitchat_private(ctx.user_text):
                        return None
                except Exception as e:
                    logger.debug("gate pending_correction chitchat exempt: %s", e)
            return "pending_correction"
    elif flag == "assistant_expects_reply":
        try:
            from core.prompt_routing import infer_assistant_expects_reply

            if infer_assistant_expects_reply(
                ctx.last_assistant_text,
                task_tier="",
                last_intent=ctx.last_intent,
            ):
                return "assistant_expects_reply"
        except Exception as e:
            logger.debug("gate assistant_expects_reply: %s", e)
    elif flag == "substantive_short_turn":
        try:
            from core.brain.user_facing_contract import classify_short_user_turn

            kind = classify_short_user_turn(
                ctx.user_text,
                ctx.recent_dialogue,
                last_assistant=ctx.last_assistant_text,
            )
            if kind == "substantive":
                return "substantive_short_turn"
        except Exception as e:
            logger.debug("gate substantive_short: %s", e)
    elif flag == "prose_narrative":
        try:
            from core.intent_heuristics import prose_narrative_disfavors_calculator

            if prose_narrative_disfavors_calculator(ctx.user_text):
                return "prose_narrative"
        except Exception as e:
            logger.debug("gate prose_narrative: %s", e)
    elif flag == "code_debug_word_only":
        low = ctx.user_text.lower()
        if "ошибка" in low and ctx.text_len > 72:
            if not re.search(
                r"traceback|exception|syntaxerror|nameerror|не компилируется|```",
                low,
            ):
                return "code_debug_word_only"
    elif flag == "article_context":
        try:
            from core.brain.profile_route_guard import (
                extract_urls,
                text_mentions_article_context,
                url_looks_like_article,
            )

            if text_mentions_article_context(ctx.user_text):
                return "article_context"
            urls = extract_urls(ctx.user_text)
            if urls and any(url_looks_like_article(u) for u in urls):
                return "article_context"
        except Exception as e:
            logger.debug("gate article_context: %s", e)
    return None


def _check_requirement(req: str, ctx: TurnDecisionContext, rule: Dict[str, Any]) -> bool:
    rid = str(rule.get("id") or "")
    if req == "explicit_geo_nearby":
        try:
            from core.geo_nearby_reply import is_explicit_nearby_request

            return is_explicit_nearby_request(ctx.user_text)
        except Exception:
            return False
    if req == "explicit_weather_query":
        try:
            from core.brain.text_helpers import task_fact_profile

            facts: Dict[str, Any] = {}
            if isinstance(ctx.persisted, dict):
                uf = ctx.persisted.get("user_facts")
                if isinstance(uf, dict):
                    facts = uf
            prof = task_fact_profile(
                ctx.user_text,
                facts,
                ctx.recent_dialogue or None,
                persisted=ctx.persisted if isinstance(ctx.persisted, dict) else None,
            )
            return bool(prof.get("is_weather"))
        except Exception:
            return False
    if req == "explicit_news_headlines_request":
        try:
            from core.brain.text_helpers import (
                looks_like_news_headlines_request,
                looks_like_pasted_news_article,
                task_fact_profile,
            )

            if looks_like_pasted_news_article(ctx.user_text):
                return False
            prof = task_fact_profile(ctx.user_text, {}, ctx.recent_dialogue)
            if prof.get("is_pasted_article"):
                return False
            return bool(prof.get("is_news") or looks_like_news_headlines_request(ctx.user_text))
        except Exception:
            return False
    if req == "news_item_pick_after_digest":
        try:
            from core.brain.text_helpers import resolve_news_item_pick_index

            return (
                resolve_news_item_pick_index(
                    ctx.user_text,
                    ctx.recent_dialogue,
                    {"dialogue_state": ctx.dialogue_state},
                )
                is not None
            )
        except Exception:
            return False
    if req == "explicit_reminder_cancel":
        try:
            from core.reminder_nl import looks_like_cancel_reminder_request

            return looks_like_cancel_reminder_request(ctx.user_text)
        except Exception:
            return False
    if req == "explicit_reminder_setup":
        if rid == "reminder_schedule":
            try:
                from core.reminder_nl import _looks_like_reminder_setup_intent
                from core.schedule_nl import parse_weekly_schedule

                if _looks_like_reminder_setup_intent(ctx.user_text):
                    return True
                return parse_weekly_schedule(ctx.user_text) is not None
            except Exception:
                return False
        return False
    if req == "fast_path_candidate":
        return bool(ctx.fast_path_candidate)
    if req == "ultra_short_text":
        return bool(ctx.ultra_short_text)
    if req == "batch_message_candidate":
        try:
            from core.brain.router_classifier import _detect_batch

            return _detect_batch(ctx.user_text)
        except Exception:
            return False
    if req == "chitchat_fast_private_candidate":
        try:
            from core.prompt_routing import is_pure_chitchat_private

            return is_pure_chitchat_private(ctx.user_text)
        except Exception:
            return False
    if req == "has_telegram_location":
        return bool(ctx.has_telegram_location)
    if req == "explicit_code_debug":
        low = (ctx.user_text or "").lower()
        if re.search(
            r"traceback|exception|syntaxerror|nameerror|typeerror|не компилируется",
            low,
        ):
            return True
        if "```" in (ctx.user_text or ""):
            return True
        if re.search(r"(?i)(?:^|\n)\s*debug\b", ctx.user_text or ""):
            return True
        if any(t in low for t in ("проверь код", "code review", "ревью кода", "найди баг")):
            return True
        return False
    if req == "explicit_legal_query":
        txt = ctx.user_text or ""
        low = txt.lower()
        if re.search(r"(?i)pravo\.by|/legal\b", txt):
            return True
        if re.search(r"(?i)(?:^|\n|\.)\s*(?:закон|нпа|кодекс|указ\s+\d)", txt):
            return True
        if re.search(r"(?i)(?:^|\n|\.)\s*(?:статья\s+\d|статьёй\s+\d|ст\.?\s*\d)", txt):
            return True
        if any(t in low for t in ("закон", "нпа", "кодекс")) and len(txt) < 120:
            return True
        return False
    if req == "explicit_math_request":
        try:
            from core.intent_heuristics import (
                explicit_math_request,
                strip_urls_and_mentions_for_math_probe,
            )

            raw = ctx.user_text or ""
            scrubbed = strip_urls_and_mentions_for_math_probe(raw)
            return explicit_math_request(raw, scrubbed)
        except Exception:
            return False
    if req == "explicit_translation_prefix":
        txt = (ctx.user_text or "").strip()
        low = txt.lower()
        if re.search(r"(?i)^(?:переведи|translate)\b", txt):
            return True
        if re.search(r"(?i)^перевод\s", low):
            return True
        return False
    if req == "explicit_summarization_request":
        try:
            from core.intent_heuristics import explicit_summarization_request

            return explicit_summarization_request(ctx.user_text)
        except Exception:
            return False
    if req == "explicit_research_request":
        try:
            from core.intent_heuristics import explicit_research_request

            return explicit_research_request(ctx.user_text)
        except Exception:
            return False
    if req == "explicit_troubleshooting_request":
        try:
            from core.intent_heuristics import explicit_troubleshooting_request

            return explicit_troubleshooting_request(ctx.user_text)
        except Exception:
            return False
    if req == "explicit_quick_explain_request":
        try:
            from core.intent_heuristics import explicit_quick_explain_request

            return explicit_quick_explain_request(ctx.user_text)
        except Exception:
            return False
    return True


def shortcut_allowed(rule_id: str, ctx: TurnDecisionContext) -> GateResult:
    rid = str(rule_id or "").strip()
    if not rid:
        return GateResult(verdict="blocked", rule_id="", reason="empty_rule_id")

    if not gate_enabled():
        return GateResult(verdict="allowed", rule_id=rid, reason="gate_disabled")

    from core.heuristic_shortcuts_registry import get_rule

    rule = get_rule(rid)
    if not rule:
        return GateResult(verdict="allowed", rule_id=rid, reason="unknown_rule_pass_through")

    domain = str(rule.get("domain") or "")
    if not _domain_matches(domain, ctx):
        return GateResult(verdict="blocked", rule_id=rid, reason="domain_mismatch")

    neg = _negative_pattern_hits(ctx.user_text, rule)
    if neg:
        return GateResult(verdict="blocked", rule_id=rid, reason=neg)

    for flag in rule.get("block_if") or []:
        if not isinstance(flag, str):
            continue
        hit = _check_block_flag(flag, ctx, rule)
        if hit:
            return GateResult(verdict="blocked", rule_id=rid, reason=hit)

    requires = rule.get("requires") or []
    if isinstance(requires, list):
        for req in requires:
            if isinstance(req, str) and not _check_requirement(req, ctx, rule):
                return GateResult(verdict="blocked", rule_id=rid, reason=f"missing:{req}")

    if (
        ctx.text_len >= _uncertain_min_chars()
        and ctx.prose_score >= 0.25
        and os.getenv("HEURISTIC_UNCERTAIN_LLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    ):
        return GateResult(verdict="uncertain", rule_id=rid, reason="uncertain_prose")

    return GateResult(verdict="allowed", rule_id=rid, reason="ok")


def should_run_shortcut(
    rule_id: str,
    user_text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    persisted: Optional[Dict[str, Any]] = None,
    planner_context: Optional[Dict[str, Any]] = None,
    fast_path_candidate: bool = False,
    ultra_short_text: bool = False,
) -> GateResult:
    ctx = build_turn_decision_context(
        user_text,
        meta=meta,
        persisted=persisted,
        planner_context=planner_context,
        fast_path_candidate=fast_path_candidate,
        ultra_short_text=ultra_short_text,
    )
    result = shortcut_allowed(rule_id, ctx)
    append_gate_audit(planner_context, result, topic_current=ctx.topic_current)
    if result.verdict != "allowed":
        try:
            from core.heuristic_misses_log import record_heuristic_miss

            uid = ""
            if isinstance(planner_context, dict):
                uid = str(planner_context.get("user_id") or "")
            record_heuristic_miss(
                rule_id=rule_id,
                verdict=result.verdict,
                reason=result.reason,
                user_text=user_text,
                topic_current=ctx.topic_current,
                user_id=uid,
            )
        except Exception as e:
            logger.debug("record_heuristic_miss: %s", e)
        logger.info(
            "[heuristic_gate] rule=%s verdict=%s reason=%s len=%s topic=%r",
            rule_id,
            result.verdict,
            result.reason,
            ctx.text_len,
            ctx.topic_current,
        )
    return result


async def should_run_shortcut_async(
    rule_id: str,
    user_text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    persisted: Optional[Dict[str, Any]] = None,
    planner_context: Optional[Dict[str, Any]] = None,
    fast_path_candidate: bool = False,
    ultra_short_text: bool = False,
) -> GateResult:
    """Gate + опциональный LLM-судья для uncertain (B3)."""
    result = should_run_shortcut(
        rule_id,
        user_text,
        meta=meta,
        persisted=persisted,
        planner_context=planner_context,
        fast_path_candidate=fast_path_candidate,
        ultra_short_text=ultra_short_text,
    )
    if result.verdict != "uncertain":
        return result
    try:
        from core.heuristic_uncertain_judge import judge_shortcut_uncertain

        ctx = build_turn_decision_context(
            user_text,
            meta=meta,
            persisted=persisted,
            planner_context=planner_context,
            fast_path_candidate=fast_path_candidate,
            ultra_short_text=ultra_short_text,
        )
        resolved = await judge_shortcut_uncertain(rule_id, ctx, user_text=user_text)
        append_gate_audit(planner_context, resolved, topic_current=ctx.topic_current)
        if resolved.verdict != "allowed":
            try:
                from core.heuristic_misses_log import record_heuristic_miss

                uid = ""
                if isinstance(planner_context, dict):
                    uid = str(planner_context.get("user_id") or "")
                record_heuristic_miss(
                    rule_id=rule_id,
                    verdict=resolved.verdict,
                    reason=resolved.reason,
                    user_text=user_text,
                    topic_current=ctx.topic_current,
                    user_id=uid,
                )
            except Exception as e:
                logger.debug("record_heuristic_miss async: %s", e)
        logger.info(
            "[heuristic_gate] rule=%s uncertain→%s reason=%s",
            rule_id,
            resolved.verdict,
            resolved.reason,
        )
        return resolved
    except Exception as e:
        logger.debug("should_run_shortcut_async: %s", e)
        blocked = GateResult(verdict="blocked", rule_id=str(rule_id or ""), reason="uncertain_resolve_error")
        append_gate_audit(planner_context, blocked, topic_current="")
        return blocked
