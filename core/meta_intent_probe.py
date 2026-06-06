"""
Короткий LLM-классификатор мета-намерения + слияние с эвристиками.

Снимает зависимость от бесконечного расширения списков ключевых слов: модель
видит формулировку и хвост диалога. При выключенном зонде или ошибке API
остаются те же эвристики, что и раньше.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def probe_enabled() -> bool:
    return _truthy("META_INTENT_PROBE_ENABLED", False)


def min_confidence() -> float:
    try:
        return max(0.0, min(1.0, float((os.getenv("META_INTENT_MIN_CONFIDENCE") or "0.5").strip() or "0.5")))
    except ValueError:
        return 0.5


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


def _dialogue_tail_for_prompt(recent_dialogue: Any, limit: int = 6) -> str:
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        return ""
    lines: List[str] = []
    for row in recent_dialogue[-limit:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "?").strip()
        text = str(row.get("text") or "").strip().replace("\n", " ")
        if len(text) > 220:
            text = text[:217] + "…"
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def classify_meta_intent_heuristic(user_text: str) -> Dict[str, Any]:
    from core.dialogue_feedback_signals import user_feedback_likely
    from core.dialogue_plot_signals import plot_twist_likely
    from core.prompt_routing import user_requests_dialogue_analysis

    t = (user_text or "").strip()
    if not t:
        return {"meta": "none", "confidence": 0.0, "source": "heuristic"}
    if plot_twist_likely(t):
        return {"meta": "plot_reset", "confidence": 0.88, "source": "heuristic"}
    if user_requests_dialogue_analysis(t):
        return {"meta": "dialogue_review", "confidence": 0.86, "source": "heuristic"}
    if user_feedback_likely(t):
        return {"meta": "user_feedback", "confidence": 0.82, "source": "heuristic"}
    return {"meta": "none", "confidence": 0.55, "source": "heuristic"}


def _normalize_meta_label(raw: str) -> str:
    s = (raw or "").strip().lower().replace("-", "_")
    if s in ("user_feedback", "feedback", "correction"):
        return "user_feedback"
    if s in ("dialogue_review", "dialogue", "transcript", "history_review"):
        return "dialogue_review"
    if s in ("plot_reset", "plot", "story_reset", "canon_reset"):
        return "plot_reset"
    return "none"


def merge_meta_intent(heuristic: Dict[str, Any], llm: Dict[str, Any]) -> Dict[str, Any]:
    """Если LLM уверен — берём его; иначе эвристику (если она не none)."""
    h_meta = str(heuristic.get("meta") or "none")
    if not llm:
        return dict(heuristic)
    l_meta = str(llm.get("meta") or "none")
    try:
        l_conf = float(llm.get("confidence", 0))
    except (TypeError, ValueError):
        l_conf = 0.0
    l_conf = max(0.0, min(1.0, l_conf))
    floor = min_confidence()
    if l_conf >= floor and l_meta != "none":
        return {"meta": l_meta, "confidence": l_conf, "source": "llm"}
    if h_meta != "none":
        return dict(heuristic)
    return {"meta": "none", "confidence": l_conf, "source": "merged"}


def dialogue_review_from_meta(input_meta: Optional[Dict[str, Any]]) -> bool:
    """Для should_backfill: запрос разобрать переписку по результату зонда."""
    if not isinstance(input_meta, dict):
        return False
    mi = input_meta.get("meta_intent")
    if not isinstance(mi, dict):
        return False
    if str(mi.get("meta") or "") != "dialogue_review":
        return False
    try:
        c = float(mi.get("confidence", 0))
    except (TypeError, ValueError):
        c = 0.0
    return c >= min_confidence()


async def fetch_meta_intent_llm(
    *,
    user_text: str,
    recent_dialogue: Any,
    telegram_reply_context: str = "",
) -> Dict[str, Any]:
    from core.openrouter_provider import get_openrouter_provider

    prov = get_openrouter_provider()
    model = (os.getenv("OPENROUTER_MODEL_META_INTENT") or "").strip() or None
    try:
        max_tok = max(64, min(200, int((os.getenv("META_INTENT_MAX_TOKENS") or "120").strip() or "120")))
    except ValueError:
        max_tok = 120
    try:
        max_ch = max(400, min(8000, int((os.getenv("META_INTENT_MAX_USER_CHARS") or "3200").strip() or "3200")))
    except ValueError:
        max_ch = 3200
    text = (user_text or "").strip()[:max_ch]
    tail = _dialogue_tail_for_prompt(recent_dialogue, 6)
    trc = (telegram_reply_context or "").strip()[:800]
    sys = (
        "Ты классификатор мета-намерения последней реплики. Ответь ТОЛЬКО JSON без markdown и без текста вокруг. "
        "Ключи: meta (строка), confidence (число 0..1). "
        "Значение meta одно из: none | user_feedback | dialogue_review | plot_reset.\n"
        "user_feedback — поправка к ответу бота: неверно, не то, съехал с темы, перечитай, неточность, не отвечаешь на вопрос; "
        "не новая отдельная задача без претензии к прошлому ответу.\n"
        "dialogue_review — разобрать переписку, историю чата, кто прав, что было выше, ретроспектива.\n"
        "plot_reset — смена канона сюжета/отношений (расстались, забудь прошлый сценарий, new scenario).\n"
        "none — обычный запрос или новая тема без мета-претензии к прошлому ответу."
    )
    user_parts = [f"Последнее сообщение пользователя:\n{text}"]
    if tail:
        user_parts.append(f"Контекст диалога (хвост):\n{tail}")
    if trc:
        user_parts.append(f"Ветка ответа (Telegram reply):\n{trc}")
    user_msg = "\n\n".join(user_parts)
    try:
        out = await prov.generate(
            prompt=user_msg,
            model=model,
            system_prompt=sys,
            max_tokens=max_tok,
            temperature=0.1,
            telemetry_tag="meta_intent",
        )
    except Exception as e:
        logger.debug("meta_intent llm: %s", e)
        return {}
    if out.get("error"):
        return {}
    obj = _parse_json_obj(str(out.get("content") or ""))
    meta = _normalize_meta_label(str(obj.get("meta") or "none"))
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.65
    conf = max(0.0, min(1.0, conf))
    return {"meta": meta, "confidence": conf, "source": "llm"}


async def compute_meta_intent_pack(
    *,
    ctx_template: Dict[str, Any],
    user_text: str,
    input_obj: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Один проход эвристик + опционально LLM. ctx_template — любой контекст шага с recent_dialogue.
    """
    ut = (user_text or "").strip()
    h = classify_meta_intent_heuristic(ut)
    if not isinstance(ctx_template, dict):
        return h
    if ctx_template.get("brain_fast_chitchat"):
        out = dict(h)
        out["source"] = "skipped_fast_chitchat"
        return out
    try:
        min_len = int(os.getenv("META_INTENT_PROBE_MIN_USER_CHARS", "4"))
    except ValueError:
        min_len = 4
    if len(ut) < min_len:
        return h
    llm: Dict[str, Any] = {}
    if probe_enabled():
        meta_in = input_obj.get("meta") if isinstance(input_obj, dict) else None
        trc = str(meta_in.get("telegram_reply_context") or "") if isinstance(meta_in, dict) else ""
        try:
            llm = await fetch_meta_intent_llm(
                user_text=ut,
                recent_dialogue=ctx_template.get("recent_dialogue"),
                telegram_reply_context=trc,
            )
        except Exception as e:
            logger.debug("meta_intent fetch: %s", e)
            llm = {}
    return merge_meta_intent(h, llm)


def apply_meta_intent_pack(
    ctx: Dict[str, Any],
    pack: Dict[str, Any],
    *,
    user_text: str,
    user_id: Optional[str],
    group_id: Optional[str],
    routing_prefs: Optional[Dict[str, Any]] = None,
    input_obj: Optional[Dict[str, Any]] = None,
) -> None:
    """Без LLM: записать pack в ctx, обновить hint и при необходимости бэкаф архива."""
    if not isinstance(ctx, dict) or not isinstance(pack, dict):
        return
    ctx["meta_intent"] = dict(pack)
    rp = routing_prefs if isinstance(routing_prefs, dict) else {}
    try:
        from core.dialogue_feedback_signals import build_user_remark_hint

        _urh = build_user_remark_hint(user_text=user_text, routing_prefs=rp, meta_intent=pack)
        if _urh:
            ctx["user_remark_hint"] = _urh
    except Exception as e:
        logger.debug("meta_intent user_remark_hint: %s", e)
    if not user_id:
        return
    try:
        from core.message_archive import maybe_backfill_context_recent_dialogue

        base_meta = {}
        if isinstance(input_obj, dict):
            m0 = input_obj.get("meta")
            if isinstance(m0, dict):
                base_meta = dict(m0)
        base_meta["meta_intent"] = dict(pack)
        maybe_backfill_context_recent_dialogue(
            ctx,
            user_id=str(user_id),
            group_id=group_id,
            user_text=user_text or "",
            input_meta=base_meta,
        )
    except Exception as e:
        logger.debug("meta_intent backfill: %s", e)
