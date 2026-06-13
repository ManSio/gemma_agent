"""
Проактивный движок сценариев: видеть риски до ответа и выбирать финал по ветке.

Фазы:
  pre_plan     — до execute_plan (подсказки мозгу, подавление ложных веток)
  post_execute — после execute_plan (дедуп, anti-intrusion, обрезка)
  pre_send     — последняя проверка текста перед Telegram

Реестр расширяется функциями _check_*; не размазывать if/else по orchestrator/input_layer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.models import Output
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

CheckFn = Callable[["TurnContext"], Optional["ScenarioHit"]]


@dataclass
class TurnContext:
    user_text: str = ""
    user_id: str = ""
    chat_id: str = ""
    group_id: Optional[str] = None
    intent: str = ""
    module: str = ""
    has_attachment: bool = False
    file_type: str = ""
    facts_flow: Optional[Dict[str, Any]] = None
    dialogue_state: Optional[Dict[str, Any]] = None
    pending_bug_report: bool = False
    pending_image: bool = False
    outputs: Optional[List[Output]] = None


@dataclass
class ScenarioHit:
    id: str
    phase: str
    severity: str  # info | warn | critical
    action: str
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnForecast:
    """Сводка рисков на ход + флаги для orchestrator/brain."""
    hits: List[ScenarioHit] = field(default_factory=list)
    suppress_fact_confirmation: bool = False
    force_anti_intrusion: bool = False
    expect_multi_answer_risk: bool = False
    prefer_news_direct: bool = False
    situation_lane: str = ""
    brain_hint_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": [
                {
                    "id": h.id,
                    "phase": h.phase,
                    "severity": h.severity,
                    "action": h.action,
                    "reason": h.reason,
                }
                for h in self.hits
            ],
            "suppress_fact_confirmation": self.suppress_fact_confirmation,
            "force_anti_intrusion": self.force_anti_intrusion,
            "expect_multi_answer_risk": self.expect_multi_answer_risk,
            "prefer_news_direct": self.prefer_news_direct,
            "situation_lane": self.situation_lane,
            "brain_hint_lines": list(self.brain_hint_lines),
        }


def forecast_from_dict(data: Optional[Dict[str, Any]]) -> TurnForecast:
    fc = TurnForecast()
    if not isinstance(data, dict):
        return fc
    fc.suppress_fact_confirmation = bool(data.get("suppress_fact_confirmation"))
    fc.force_anti_intrusion = bool(data.get("force_anti_intrusion"))
    fc.expect_multi_answer_risk = bool(data.get("expect_multi_answer_risk"))
    fc.prefer_news_direct = bool(data.get("prefer_news_direct"))
    fc.situation_lane = str(data.get("situation_lane") or "").strip()
    for ln in data.get("brain_hint_lines") or []:
        if isinstance(ln, str) and ln.strip():
            fc.brain_hint_lines.append(ln.strip())
    for h in data.get("hits") or []:
        if not isinstance(h, dict):
            continue
        fc.hits.append(
            ScenarioHit(
                id=str(h.get("id") or ""),
                phase=str(h.get("phase") or ""),
                severity=str(h.get("severity") or "info"),
                action=str(h.get("action") or ""),
                reason=str(h.get("reason") or ""),
            )
        )
    return fc


def enabled() -> bool:
    return effective_bool("SCENARIO_ENGINE_ENABLED", default=True)


# --- PRE_PLAN detectors ---


def _check_user_feedback_reset(ctx: TurnContext) -> Optional[ScenarioHit]:
    try:
        from core.dialogue_feedback_signals import user_feedback_likely
    except Exception:
        return None
    if not user_feedback_likely(ctx.user_text):
        return None
    return ScenarioHit(
        id="user_feedback_reset",
        phase="pre_plan",
        severity="warn",
        action="brain_hint",
        reason="Пользователь поправляет прошлый ответ — не продолжать старую ветку.",
        meta={"hint": "Считай реплику исправлением: не повторяй прошлый tool-chain и не навязывай уточнения."},
    )


def _check_reference_paste(ctx: TurnContext) -> Optional[ScenarioHit]:
    try:
        from core.brain.router_classifier import _is_reference_paste
    except Exception:
        return None
    if not _is_reference_paste(ctx.user_text):
        return None
    return ScenarioHit(
        id="reference_paste",
        phase="pre_plan",
        severity="info",
        action="suppress_service_nags",
        reason="Длинная вставка — не факты/напоминания.",
    )


def _check_finance_not_city(ctx: TurnContext) -> Optional[ScenarioHit]:
    try:
        from core.user_facts import _CITY_FINANCE_CONTAMINATION_RE, _message_looks_unrelated_to_profile_facts
    except Exception:
        return None
    low = (ctx.user_text or "").lower()
    if not _CITY_FINANCE_CONTAMINATION_RE.search(low):
        return None
    if _message_looks_unrelated_to_profile_facts(ctx.user_text):
        return ScenarioHit(
            id="finance_not_city",
            phase="pre_plan",
            severity="warn",
            action="suppress_fact_confirmation",
            reason="Инвестиционный текст — не подтверждение города.",
        )
    return None


def _check_reminder_in_prose(ctx: TurnContext) -> Optional[ScenarioHit]:
    low = (ctx.user_text or "").lower()
    if len(low) < 80:
        return None
    if not re.search(r"напоминан", low):
        return None
    if re.search(r"(?i)^\s*напомни\b|напомни\s+мне|поставь\s+напомин", low):
        return None
    return ScenarioHit(
        id="reminder_word_in_prose",
        phase="pre_plan",
        severity="warn",
        action="brain_hint",
        reason="«напоминание» в тексте статьи, не команда боту.",
        meta={"hint": "Не предлагай создать reminder и не спрашивай время — это обсуждение чужого текста."},
    )


def _check_news_turn(ctx: TurnContext) -> Optional[ScenarioHit]:
    try:
        from core.brain.text_helpers import (
            looks_like_news_headlines_request,
            looks_like_pasted_news_article,
            task_fact_profile,
        )
    except Exception:
        return None
    ut = ctx.user_text or ""
    if looks_like_pasted_news_article(ut):
        return None
    tf = task_fact_profile(ut, {}, [])
    if not tf.get("is_news") and ctx.intent not in ("news", "news_brief"):
        if not looks_like_news_headlines_request(ut):
            return None
    return ScenarioHit(
        id="news_turn",
        phase="pre_plan",
        severity="info",
        action="prefer_news_direct",
        reason="Запрос новостей — опора на поиск, без выдуманных пунктов.",
    )


def _check_multi_answer_risk(ctx: TurnContext) -> Optional[ScenarioHit]:
    """Два вопроса или смешение тем → риск двух несвязанных ответов."""
    t = (ctx.user_text or "").strip()
    if len(t) < 40:
        return None
    qmarks = t.count("?")
    if qmarks >= 2 or re.search(r"(?i)и\s+ещё|а\s+также|второй\s+вопрос", t):
        return ScenarioHit(
            id="multi_question",
            phase="pre_plan",
            severity="warn",
            action="expect_multi_answer",
            reason="Несколько вопросов — один связный ответ, не два монолога.",
            meta={"hint": "Ответь на все части в одном сообщении; не переключайся на другую тему."},
        )
    return None


def _check_substantive_question_hint(ctx: TurnContext) -> Optional[ScenarioHit]:
    if not _user_asks_substantive_question(ctx.user_text):
        return None
    return ScenarioHit(
        id="substantive_question",
        phase="pre_plan",
        severity="info",
        action="brain_hint",
        reason="Вопрос требует содержательного ответа, не односложного.",
        meta={
            "hint": (
                "Пользователь задал вопрос «почему/зачем/что такое» — дай объяснение "
                "(1–4 предложения). Не отвечай только «ок», «да» или «нет»."
            ),
        },
    )


def _check_bug_report_pending(ctx: TurnContext) -> Optional[ScenarioHit]:
    if not ctx.pending_bug_report:
        return None
    return ScenarioHit(
        id="bug_report_pending",
        phase="pre_plan",
        severity="info",
        action="brain_hint",
        reason="Ожидается описание бага или отмена.",
        meta={"hint": "Если текст не про баг — не оформляй bug report."},
    )


_PRE_CHECKS: List[CheckFn] = [
    _check_user_feedback_reset,
    _check_reference_paste,
    _check_finance_not_city,
    _check_reminder_in_prose,
    _check_news_turn,
    _check_multi_answer_risk,
    _check_substantive_question_hint,
    _check_bug_report_pending,
]


# --- POST_EXECUTE detectors ---

_BRIEF_USER_RE = re.compile(
    r"(одним\s+словом|только\s*:\s*|только\s+числ|only\s+(?:a\s+)?number|"
    r"коротко|лаконично|в\s+одно\s+слово|ответь\s+одним|say\s+only|just\s+say)",
    re.IGNORECASE,
)
_NUMERIC_ONLY_REPLY_RE = re.compile(r"^\d+(?:[.,]\d+)?\s*[\.\!]?\s*$")
_DIRECT_REPLY_SHORT_OK = frozenset(
    {
        "referential_math",
        "news_web_search",
        "news_direct",
        "news_item_direct",
        "weather_direct",
        "geo_nearby",
        "telegram_location",
        "nl_reminder",
        "nl_cancel_reminder",
        "nl_weekly_schedule",
        "affirmative_search",
    }
)
_EXPLAIN_Q_RE = re.compile(
    r"(?i)\b(почему|зачем|отчего|как\s+работает|что\s+такое|объясни|расскажи\s+почему)\b",
)
_CAPITAL_Q_RE = re.compile(
    r"(?i)(?:столиц\w*|capital\s+of|what\s+is\s+the\s+capital)",
)
_PLACE_NAME_BODY_RE = re.compile(
    r"^[\s«»\"'„“]*([A-Za-zА-Яа-яЁёІіЎўŁł\-]+(?:\s+[A-Za-zА-Яа-яЁёІіЎўŁł\-]+){0,3})[\s«»\"'„“]*[.!?…]*$",
)
_SHORT_REPLY_OK = frozenset(
    {
        "ok",
        "ок",
        "да",
        "нет",
        "yes",
        "no",
        "ага",
        "угу",
        "ладно",
    }
)
_GREETING_SHORT = frozenset(
    {
        "hi",
        "hey",
        "привет",
        "пока",
        "спасибо",
        "thanks",
        "thx",
    }
)


def _user_wants_brief_reply(user_text: str) -> bool:
    return bool(_BRIEF_USER_RE.search(user_text or ""))


def _user_asks_substantive_question(user_text: str) -> bool:
    return bool(_EXPLAIN_Q_RE.search(user_text or ""))


def _user_asks_capital(user_text: str) -> bool:
    return bool(_CAPITAL_Q_RE.search(user_text or ""))


def _is_short_place_name_reply(body: str) -> bool:
    """Короткий ответ-название (Минск, Paris) — не пустой и не «ок»."""
    b = (body or "").strip()
    if not b or len(b) > 48:
        return False
    if _is_trivial_ack_body(b):
        return False
    m = _PLACE_NAME_BODY_RE.match(b)
    if not m:
        return False
    name = (m.group(1) or "").strip()
    return len(name) >= 2


def _polish_capital_short_reply(body: str, user_text: str) -> str:
    """Развернуть слишком короткий, но верный ответ про столицу."""
    b = (body or "").strip()
    if not b or not _user_asks_capital(user_text):
        return b
    low = (user_text or "").lower()
    norm = b.lower().rstrip(".,!?…")
    if "минск" in low and norm.startswith("минск"):
        return (
            "Столица Беларуси — Минск. "
            "Сам Минск — город; отдельной «столицы Минска» не бывает — обычно имеют в виду столицу страны."
        )
    if _is_short_place_name_reply(b):
        return f"Столица — {b.rstrip('.!?…')}."
    return b


def _is_trivial_ack_body(body: str) -> bool:
    norm = (body or "").strip().lower().rstrip(".,!?…")
    return bool(norm) and norm in _SHORT_REPLY_OK


def _is_mismatched_trivial_ack(body: str, user_text: str) -> bool:
    """«ок»/«да» на вопрос «почему…» без просьбы ответить кратко."""
    if not _is_trivial_ack_body(body):
        return False
    if _user_wants_brief_reply(user_text):
        return False
    if _user_asks_substantive_question(user_text):
        return True
    return False


def _is_acceptable_short_reply(
    body: str,
    user_text: str = "",
    *,
    recent_dialogue: Any = None,
    last_assistant: str = "",
) -> bool:
    """Короткий, но осмысленный ответ — не считать пустым."""
    b = (body or "").strip()
    if not b:
        return False
    if _is_mismatched_trivial_ack(b, user_text):
        return False
    try:
        from core.brain.user_facing_contract import short_reply_acceptable_for_turn

        if short_reply_acceptable_for_turn(
            b, user_text, recent_dialogue, last_assistant=last_assistant
        ):
            return True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    norm = b.lower().rstrip(".,!?…")
    if norm in _GREETING_SHORT:
        return True
    if _user_wants_brief_reply(user_text):
        if norm in _SHORT_REPLY_OK or (len(norm) <= 6 and norm.replace("-", "").isalnum()):
            return True
        if len(b) <= 48:
            return True
    if _user_asks_capital(user_text) and _is_short_place_name_reply(b):
        return True
    if _NUMERIC_ONLY_REPLY_RE.match(b) and _BRIEF_USER_RE.search(user_text or ""):
        return True
    return False


def _check_duplicate_outputs(ctx: TurnContext) -> Optional[ScenarioHit]:
    outputs = ctx.outputs or []
    substantive = [
        o
        for o in outputs
        if o.type == "text"
        and len(str(o.payload or "").strip()) >= 80
        and not bool((o.meta or {}).get("confirmation"))
    ]
    if len(substantive) < 2:
        return None
    return ScenarioHit(
        id="duplicate_substantive_outputs",
        phase="post_execute",
        severity="warn",
        action="dedupe_outputs",
        reason="Два содержательных ответа на один ход.",
    )


def _check_intrusive_tail(ctx: TurnContext) -> Optional[ScenarioHit]:
    outputs = ctx.outputs or []
    if not outputs:
        return None
    try:
        from core.input_layer import _has_substantive_assistant_reply, _looks_like_long_prose_discussion
    except Exception:
        return None
    if _has_substantive_assistant_reply(outputs) or _looks_like_long_prose_discussion(ctx.user_text):
        return ScenarioHit(
            id="intrusive_service_tail",
            phase="post_execute",
            severity="info",
            action="anti_intrusion",
            reason="Есть основной ответ — убрать сервисные хвосты.",
        )
    return None


def _recent_for_turn(ctx: TurnContext) -> Any:
    ds = ctx.dialogue_state if isinstance(ctx.dialogue_state, dict) else {}
    return ds.get("recent_dialogue") or ds.get("recent_messages")


def _last_assistant_for_turn(ctx: TurnContext) -> str:
    ds = ctx.dialogue_state if isinstance(ctx.dialogue_state, dict) else {}
    return str(ds.get("last_assistant_excerpt") or "").strip()


def _check_empty_output(ctx: TurnContext) -> Optional[ScenarioHit]:
    _rd = _recent_for_turn(ctx)
    _la = _last_assistant_for_turn(ctx)
    for o in ctx.outputs or []:
        if o.type != "text":
            continue
        if bool((o.meta or {}).get("confirmation")):
            continue
        _meta = o.meta if isinstance(o.meta, dict) else {}
        if str(_meta.get("reason") or "") in _DIRECT_REPLY_SHORT_OK:
            continue
        body = str(o.payload or "").strip()
        if not body:
            return ScenarioHit(
                id="empty_output",
                phase="post_execute",
                severity="critical",
                action="pre_send_recover",
                reason="Пустой текстовый ответ.",
            )
        if len(body) < 8 and not _is_acceptable_short_reply(
            body, ctx.user_text, recent_dialogue=_rd, last_assistant=_la
        ):
            return ScenarioHit(
                id="empty_output",
                phase="post_execute",
                severity="critical",
                action="pre_send_recover",
                reason="Почти пустой текстовый ответ.",
            )
    return None


def _check_leak_in_output(ctx: TurnContext) -> Optional[ScenarioHit]:
    try:
        from core.brain.response_finalize import looks_like_prompt_instruction_leak
    except Exception:
        return None
    for o in ctx.outputs or []:
        if o.type != "text":
            continue
        body = str(o.payload or "").strip()
        if body and looks_like_prompt_instruction_leak(body):
            return ScenarioHit(
                id="leak_in_output",
                phase="post_execute",
                severity="critical",
                action="pre_send_recover",
                reason="Утечка инструкции промпта в ответе.",
            )
    return None


_POST_CHECKS: List[CheckFn] = [
    _check_duplicate_outputs,
    _check_intrusive_tail,
    _check_empty_output,
    _check_leak_in_output,
]

_TRUNCATION_NOTE = "\n\n_(Ответ мог оборваться — напиши «Продолжи», если нужно дописать.)_"


def _delivery_fallback(
    user_text: str,
    *,
    recent_dialogue: Any = None,
    last_assistant: str = "",
    reason: str = "empty",
) -> str:
    try:
        from core.brain.user_facing_contract import recover_delivery_fallback

        return recover_delivery_fallback(
            user_text,
            recent_dialogue,
            last_assistant=last_assistant,
            reason=reason,
        )
    except Exception:
        return (
            "Не удалось сформировать ответ. Переформулируй запрос короче или уточни одну цель."
        )


def _apply_delivery_normalize(
    outputs: List[Output],
    user_text: str,
    *,
    recent_dialogue: Any = None,
    last_assistant: str = "",
) -> List[Output]:
    try:
        from core.brain.user_facing_contract import (
            classify_short_user_turn,
            normalize_user_facing_text,
        )
    except Exception:
        return outputs

    polished: List[Output] = []
    kind = classify_short_user_turn(
        user_text, recent_dialogue, last_assistant=last_assistant
    )
    for o in outputs:
        if o.type != "text":
            polished.append(o)
            continue
        raw = str(o.payload or "")
        norm = normalize_user_facing_text(raw, user_text=user_text)
        meta = dict(o.meta or {})
        meta["delivery_normalize_status"] = norm.status
        meta["short_turn_kind"] = kind
        if norm.stripped_think_tags:
            meta["delivery_stripped_think"] = True
        body = norm.text if norm.status == "ok" and norm.text else raw
        if norm.status == "ok" and norm.text:
            meta["delivery_normalized"] = True
        polished.append(Output(type="text", payload=body, meta=meta))
    return polished


def forecast_pre_turn(ctx: TurnContext) -> TurnForecast:
    fc = TurnForecast()
    if not enabled():
        return fc
    try:
        from core.situation_playbook import apply_situation_to_forecast, match_situation

        entry = match_situation(ctx)
        if entry:
            from core.situation_playbook import prose_blocks_playbook_lane

            apply_situation_to_forecast(fc, entry, ctx)
            hints_only = prose_blocks_playbook_lane(ctx)
            fc.hits.append(
                ScenarioHit(
                    id=entry.id,
                    phase="pre_plan",
                    severity="info",
                    action="situation_hints_only" if hints_only else "situation_lane",
                    reason=(
                        f"Ситуация «{entry.id}» → hints only (prose)"
                        if hints_only
                        else f"Ситуация «{entry.id}» → lane {entry.lane}"
                    ),
                    meta={"lane": entry.lane, "hints_only": hints_only},
                )
            )
    except Exception as e:
        logger.debug("situation_playbook: %s", e)
    for fn in _PRE_CHECKS:
        try:
            hit = fn(ctx)
        except Exception as e:
            logger.debug("scenario pre %s: %s", getattr(fn, "__name__", ""), e)
            continue
        if not hit:
            continue
        fc.hits.append(hit)
        act = hit.action
        if act == "suppress_fact_confirmation":
            fc.suppress_fact_confirmation = True
        if act in ("suppress_service_nags", "brain_hint") and hit.phase == "pre_plan":
            fc.force_anti_intrusion = True
        if act == "expect_multi_answer":
            fc.expect_multi_answer_risk = True
        if act == "prefer_news_direct":
            fc.prefer_news_direct = True
        hint = hit.meta.get("hint") if isinstance(hit.meta, dict) else None
        if isinstance(hint, str) and hint.strip():
            fc.brain_hint_lines.append(hint.strip())
        elif hit.reason and act == "brain_hint":
            fc.brain_hint_lines.append(hit.reason)
    return fc


def apply_forecast_to_facts_flow(facts_flow: Dict[str, Any], forecast: TurnForecast) -> Dict[str, Any]:
    if not isinstance(facts_flow, dict) or not forecast.suppress_fact_confirmation:
        return facts_flow if isinstance(facts_flow, dict) else {}
    out = dict(facts_flow)
    out.pop("confirmation_prompt", None)
    return out


def build_brain_scenario_addon(forecast: TurnForecast) -> str:
    if not forecast.brain_hint_lines:
        return ""
    lines = ["SCENARIO_FORECAST (обязательно учти):"]
    for i, ln in enumerate(forecast.brain_hint_lines[:6], 1):
        lines.append(f"{i}. {ln}")
    return "\n".join(lines)


def apply_post_execute(
    outputs: List[Output],
    user_text: str,
    forecast: Optional[TurnForecast] = None,
    *,
    recent_dialogue: Any = None,
    last_assistant: str = "",
) -> Tuple[List[Output], List[ScenarioHit], bool]:
    """Единая пост-обработка исходящих сообщений. Третий элемент — silent_skip (не слать в Telegram)."""
    hits: List[ScenarioHit] = []
    silent_skip = False
    if not enabled():
        return outputs, hits, silent_skip

    ctx = TurnContext(user_text=user_text, outputs=list(outputs))
    if recent_dialogue is not None or last_assistant:
        ds: Dict[str, Any] = {}
        if recent_dialogue is not None:
            ds["recent_dialogue"] = recent_dialogue
        if last_assistant:
            ds["last_assistant_excerpt"] = last_assistant
        ctx.dialogue_state = ds
    for fn in _POST_CHECKS:
        try:
            hit = fn(ctx)
        except Exception as e:
            logger.debug("scenario post %s: %s", getattr(fn, "__name__", ""), e)
            continue
        if hit:
            hits.append(hit)

    if forecast:
        hits.extend([h for h in forecast.hits if h.phase == "post_execute"])

    out = list(outputs)
    actions = {h.action for h in hits}
    if forecast and forecast.force_anti_intrusion:
        actions.add("anti_intrusion")
    if forecast and forecast.expect_multi_answer_risk:
        actions.add("dedupe_outputs")

    try:
        from core.input_layer import _apply_anti_intrusion_guard

        if "anti_intrusion" in actions or "suppress_service_nags" in actions:
            out, silent_skip = _apply_anti_intrusion_guard(user_text, out)
    except Exception as e:
        logger.debug("scenario anti_intrusion: %s", e)

    try:
        from core.telegram_output_guard import (
            dedupe_identical_text_outputs,
            dedupe_telegram_outputs,
        )

        if "dedupe_outputs" in actions or len(
            [o for o in out if o.type == "text" and len(str(o.payload or "")) >= 80]
        ) >= 2:
            out = dedupe_telegram_outputs(out, user_text)
        out = dedupe_identical_text_outputs(out)
    except Exception as e:
        logger.debug("scenario dedupe: %s", e)

    out = _apply_ru_weather_text_polish(out)

    if any(h.action == "pre_send_recover" for h in hits):
        out = _recover_outputs_in_place(
            out,
            user_text,
            recent_dialogue=recent_dialogue,
            last_assistant=last_assistant,
        )
    else:
        out = _apply_capital_short_polish(out, user_text)

    out = _apply_delivery_normalize(
        out,
        user_text,
        recent_dialogue=recent_dialogue,
        last_assistant=last_assistant,
    )

    ctx.outputs = out
    return out, hits, silent_skip


_GLUED_DAY_RE = re.compile(
    r"(?ui)([а-яё]+(?:е|и|у|ой|ом))(?=(сегодня|завтра|послезавтра)\b)"
)


def _fix_glued_day_adverb(body: str) -> str:
    """«В Минскесегодня» → «В Минске сегодня» (типичная склейка LLM)."""
    return _GLUED_DAY_RE.sub(r"\1 \2", body or "")


def _apply_ru_weather_text_polish(outputs: List[Output]) -> List[Output]:
    polished: List[Output] = []
    for o in outputs:
        if o.type != "text":
            polished.append(o)
            continue
        body = str(o.payload or "")
        fixed = _fix_glued_day_adverb(body)
        if fixed == body:
            polished.append(o)
            continue
        meta = dict(o.meta or {})
        meta["scenario_polished"] = True
        polished.append(Output(type="text", payload=fixed, meta=meta))
    return polished


def _apply_capital_short_polish(outputs: List[Output], user_text: str) -> List[Output]:
    if not _user_asks_capital(user_text):
        return outputs
    polished: List[Output] = []
    for o in outputs:
        if o.type != "text":
            polished.append(o)
            continue
        body = str(o.payload or "").strip()
        if not body or not _is_short_place_name_reply(body):
            polished.append(o)
            continue
        new_body = _polish_capital_short_reply(body, user_text)
        if new_body == body:
            polished.append(o)
            continue
        meta = dict(o.meta or {})
        meta["scenario_polished"] = True
        polished.append(Output(type="text", payload=new_body, meta=meta))
    return polished


def _recover_outputs_in_place(
    outputs: List[Output],
    user_text: str,
    *,
    recent_dialogue: Any = None,
    last_assistant: str = "",
) -> List[Output]:
    fixed: List[Output] = []
    for o in outputs:
        if o.type != "text":
            fixed.append(o)
            continue
        body = str(o.payload or "").strip()
        if body and _user_asks_capital(user_text) and _is_short_place_name_reply(body):
            body = _polish_capital_short_reply(body, user_text)
        if body and not _body_needs_recovery(
            body,
            user_text,
            recent_dialogue=recent_dialogue,
            last_assistant=last_assistant,
        ):
            meta = dict(o.meta or {})
            if body != str(o.payload or "").strip():
                meta["scenario_polished"] = True
            fixed.append(Output(type="text", payload=body, meta=meta))
            continue
        repl = _recover_leak_or_code(user_text, body) or _delivery_fallback(
            user_text,
            recent_dialogue=recent_dialogue,
            last_assistant=last_assistant,
        )
        meta = dict(o.meta or {})
        meta["scenario_recovered"] = True
        fixed.append(Output(type="text", payload=repl, meta=meta))
    return fixed if fixed else outputs


def _body_needs_recovery(
    body: str,
    user_text: str = "",
    *,
    recent_dialogue: Any = None,
    last_assistant: str = "",
) -> bool:
    if _is_mismatched_trivial_ack(body, user_text):
        return True
    if _is_acceptable_short_reply(
        body, user_text, recent_dialogue=recent_dialogue, last_assistant=last_assistant
    ):
        return False
    if len(body) < 8:
        return True
    try:
        from core.text_leak_scan import outbound_has_blocking_leak

        return bool(outbound_has_blocking_leak(body))
    except Exception:
        return False


def apply_pre_send(
    text: str,
    *,
    user_text: str = "",
    output_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[ScenarioHit]]:
    """Последняя проверка перед Telegram: пустота, утечка, обрыв."""
    hits: List[ScenarioHit] = []
    if not enabled():
        return (text or "").strip(), hits

    txt = (text or "").strip()
    _pre_norm_txt = txt
    ut = (user_text or "").strip()
    if output_meta and not ut:
        ut = str(output_meta.get("user_text") or output_meta.get("payload") or "").strip()

    _rd_pre: Any = None
    _la_pre = ""
    if output_meta:
        _rd_pre = output_meta.get("recent_dialogue")
        _la_pre = str(output_meta.get("last_assistant_excerpt") or "").strip()
        if not _rd_pre:
            _uid_rd = str(output_meta.get("user_id") or "").strip()
            if _uid_rd:
                try:
                    from core.behavior_store import BehaviorStore

                    _rec_rd = BehaviorStore().load(
                        _uid_rd, output_meta.get("group_id")
                    )
                    _rd_pre = _rec_rd.get("recent_messages")
                    if not _la_pre:
                        for _row in reversed(_rec_rd.get("recent_messages") or []):
                            if not isinstance(_row, dict):
                                continue
                            if str(_row.get("role") or "").lower() == "assistant":
                                _la_pre = str(
                                    _row.get("text") or _row.get("content") or ""
                                ).strip()
                                break
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    _norm_status = ""
    _short_kind = ""
    try:
        from core.brain.user_facing_contract import (
            classify_short_user_turn,
            normalize_user_facing_text,
        )

        _short_kind = classify_short_user_turn(ut, _rd_pre, last_assistant=_la_pre)
        _pre_norm_txt = txt
        _norm = normalize_user_facing_text(txt, user_text=ut)
        _norm_status = _norm.status
        txt = _norm.text if _norm.status == "ok" else ""
        if output_meta is not None and isinstance(output_meta, dict):
            output_meta["delivery_normalize_status"] = _norm.status
            output_meta["short_turn_kind"] = _short_kind
            if _norm.stripped_think_tags:
                output_meta["delivery_stripped_think"] = True
        try:
            from core.brain.code_empty_recovery import (
                code_reply_incomplete,
                resolve_code_delivery_fallback,
            )

            _need_code_fb = code_reply_incomplete(ut, txt) or code_reply_incomplete(
                ut, _pre_norm_txt
            )
            if _need_code_fb:
                code_fb = resolve_code_delivery_fallback(ut)
                if code_fb:
                    txt = code_fb
                    hits.append(
                        ScenarioHit(
                            id="pre_send_code_fallback",
                            phase="pre_send",
                            severity="warn",
                            action="code_fallback",
                            reason=f"normalize_{_norm.status or 'incomplete'}",
                        )
                    )
        except Exception as e:
            logger.debug("pre_send code_fallback: %s", e)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    if _is_mismatched_trivial_ack(txt, ut):
        hits.append(
            ScenarioHit(
                id="pre_send_trivial_ack",
                phase="pre_send",
                severity="critical",
                action="replace_fallback",
                reason="Односложный ответ на содержательный вопрос.",
            )
        )
        txt = _delivery_fallback(ut, recent_dialogue=_rd_pre, last_assistant=_la_pre)

    if not txt:
        hits.append(
            ScenarioHit(
                id="pre_send_empty",
                phase="pre_send",
                severity="critical",
                action="replace_fallback",
                reason="Пустой текст после finalize.",
            )
        )
        txt = _delivery_fallback(ut, recent_dialogue=_rd_pre, last_assistant=_la_pre)

    try:
        from core.text_leak_scan import outbound_has_blocking_leak, primary_blocking_leak_code

        _leak_src = txt
        _leak_code = primary_blocking_leak_code(_leak_src) if _leak_src else None
        if not _leak_code and _pre_norm_txt and _pre_norm_txt != txt:
            _leak_code = primary_blocking_leak_code(_pre_norm_txt)
            if _leak_code:
                _leak_src = _pre_norm_txt
        if _leak_code or (_leak_src and outbound_has_blocking_leak(_leak_src)):
            _leak_code = _leak_code or "leak"
            hits.append(
                ScenarioHit(
                    id="pre_send_leak",
                    phase="pre_send",
                    severity="critical",
                    action="replace_fallback",
                    reason=f"Утечка в финальном тексте ({_leak_code}).",
                )
            )
            recovered = _recover_leak_or_code(
                ut, _leak_src or txt, output_meta=output_meta
            )
            txt = recovered or _delivery_fallback(
                ut, recent_dialogue=_rd_pre, last_assistant=_la_pre, reason="leak"
            )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    try:
        from core.brain.text_helpers import (
            looks_like_news_headlines_request,
            looks_like_tool_execution_report_leak,
        )
        from core.news_reply import repair_news_tool_narration_reply_sync

        if txt and looks_like_tool_execution_report_leak(txt) and looks_like_news_headlines_request(ut):
            _uid_rep = str((output_meta or {}).get("user_id") or "").strip()
            _body_rep = str((output_meta or {}).get("news_search_body") or "").strip()
            _fixed = repair_news_tool_narration_reply_sync(
                txt,
                user_query=ut,
                search_body=_body_rep,
                user_id=_uid_rep,
            )
            if _fixed:
                hits.append(
                    ScenarioHit(
                        id="pre_send_news_tool_leak",
                        phase="pre_send",
                        severity="warn",
                        action="news_tool_leak_repair",
                        reason="Сырой пересказ инструмента заменён дайджестом.",
                    )
                )
                txt = _fixed
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    try:
        from core.brain.text_helpers import looks_like_tool_list_leak

        if txt and looks_like_tool_list_leak(txt):
            hits.append(
                ScenarioHit(
                    id="pre_send_tool_list_leak",
                    phase="pre_send",
                    severity="critical",
                    action="replace_fallback",
                    reason="Перечисление инструментов вместо ответа.",
                )
            )
            txt = _delivery_fallback(
                ut, recent_dialogue=_rd_pre, last_assistant=_la_pre, reason="leak"
            )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    try:
        from core.product_behavior import (
            assistant_reply_issues,
            recover_reply_for_issues,
        )

        _last_asst = ""
        if output_meta:
            _last_asst = str(output_meta.get("last_assistant_excerpt") or "").strip()
            if not _last_asst:
                _uid_pb = str(output_meta.get("user_id") or "").strip()
                if _uid_pb:
                    try:
                        from core.behavior_store import BehaviorStore

                        _rec_pb = BehaviorStore().load(
                            _uid_pb, output_meta.get("group_id")
                        )
                        for _row in reversed(_rec_pb.get("recent_messages") or []):
                            if not isinstance(_row, dict):
                                continue
                            if str(_row.get("role") or "").lower() == "assistant":
                                _last_asst = str(
                                    _row.get("text") or _row.get("content") or ""
                                ).strip()
                                break
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
        _pb_issues = assistant_reply_issues(ut, txt, _last_asst)
        if _pb_issues:
            hits.append(
                ScenarioHit(
                    id="pre_send_product_behavior",
                    phase="pre_send",
                    severity="critical",
                    action="replace_fallback",
                    reason=f"product_behavior:{','.join(_pb_issues)}",
                )
            )
            txt = recover_reply_for_issues(ut, txt, _pb_issues)
            if output_meta is not None and isinstance(output_meta, dict):
                prev_i = output_meta.get("product_behavior_issues")
                if isinstance(prev_i, list):
                    output_meta["product_behavior_issues"] = prev_i + _pb_issues
                else:
                    output_meta["product_behavior_issues"] = list(_pb_issues)
    except Exception as e:
        logger.debug("product_behavior pre_send: %s", e)

    try:
        from core.outbound_thread_guard import (
            detect_thread_followup_issues,
            recover_thread_followup_reply,
        )

        _tf_issues = detect_thread_followup_issues(ut, txt, output_meta)
        if _tf_issues:
            hits.append(
                ScenarioHit(
                    id="pre_send_thread_followup",
                    phase="pre_send",
                    severity="critical",
                    action="replace_fallback",
                    reason=f"thread_guard:{','.join(_tf_issues)}",
                )
            )
            txt = recover_thread_followup_reply(
                ut, txt, _tf_issues, output_meta=output_meta
            )
            if output_meta is not None and isinstance(output_meta, dict):
                output_meta["outbound_thread_guard_issues"] = list(_tf_issues)
    except Exception as e:
        logger.debug("outbound_thread_guard pre_send: %s", e)

    try:
        from core.input_layer import _reply_suspect_incomplete

        if _reply_suspect_incomplete(txt) and _TRUNCATION_NOTE not in txt:
            hits.append(
                ScenarioHit(
                    id="pre_send_truncated",
                    phase="pre_send",
                    severity="warn",
                    action="append_truncation_note",
                    reason="Подозрение на обрыв генерации.",
                )
            )
            txt = txt.rstrip() + _TRUNCATION_NOTE
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    return txt.strip(), hits


def _recover_leak_or_code(
    user_text: str,
    leaked: str,
    *,
    output_meta: Optional[Dict[str, Any]] = None,
) -> str:
    try:
        from core.brain.text_helpers import looks_like_news_headlines_request

        if looks_like_news_headlines_request(user_text or ""):
            from core.news_reply import repair_news_tool_narration_reply_sync

            _uid = str((output_meta or {}).get("user_id") or "").strip()
            _body = str((output_meta or {}).get("news_search_body") or "").strip()
            fixed = repair_news_tool_narration_reply_sync(
                leaked,
                user_query=user_text or "",
                search_body=_body,
                user_id=_uid,
            )
            if fixed:
                return fixed
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    try:
        from core.brain.code_empty_recovery import resolve_code_delivery_fallback

        got = resolve_code_delivery_fallback(user_text)
        if got:
            return got
    except Exception as e:
        logger.debug('%s optional failed: %s', 'scenario_engine', e, exc_info=True)
    return ""


def merge_hits(forecast: TurnForecast, post_hits: List[ScenarioHit]) -> List[Dict[str, Any]]:
    all_hits = list(forecast.hits) + list(post_hits)
    return [
        {"id": h.id, "phase": h.phase, "severity": h.severity, "action": h.action}
        for h in all_hits
    ]
