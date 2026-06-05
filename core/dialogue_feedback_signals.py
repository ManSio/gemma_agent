"""
Сигналы обратной связи: пользователь поправляет ответ, недоволен, просит перечитать,
указывает на съезд с темы / неточность (без второго LLM и без длинных вставок в system).
Подмешивается в external_hint и копится в routing_prefs.recent_user_remarks (без деплоя кода).
"""
from __future__ import annotations

import os
from typing import Any, Dict, FrozenSet, List

_FEEDBACK_SUBSTR: FrozenSet[str] = frozenset(
    {
        "не то",
        "не так",
        "неправильно",
        "ты неправ",
        "ты не прав",
        "ошибаешься",
        "ошибся",
        "ошиблась",
        "перечитай",
        "перечитайте",
        "внимательнее",
        "слушай внимательно",
        "ты не понял",
        "ты не поняла",
        "не понял меня",
        "не то имел в виду",
        "не это имел в виду",
        "я имел в виду",
        "я имела в виду",
        "имелось в виду",
        "исправься",
        "исправь ответ",
        "забудь свой",
        "забудь предыдущ",
        "не надо мне план",
        "хватит план",
        "стоп, ",
        "погоди, ",
        "that's wrong",
        "not what i meant",
        "i meant",
        "you misunderstood",
        "read again",
        "listen carefully",
        # съезд с темы / несоответствие вопросу
        "сьехал с тем",
        "съехал с тем",
        "съехал с темы",
        "с темы съехал",
        "не по теме",
        "не в тему",
        "мимо темы",
        "мимо вопроса",
        "не на вопрос",
        "не отвечаешь на вопрос",
        "не ответил на вопрос",
        "ушел от тем",
        "ушёл от тем",
        "от темы ушел",
        "от темы ушёл",
        "off topic",
        "off-topic",
        "not what i asked",
        "not answering the question",
        "didn't answer the question",
        # неточность / недостоверность ответа
        "неточн",
        "вводишь в заблуждение",
        "галлюцинац",
        "приврал",
        "выдумал факт",
        "не подтвержден",
        "не подтверждён",
        "hallucinat",
        "обрезал",
        "обрезан",
        "хорошо посмотри",
        "посмотришь",
        "посмотри ещё",
        "посмотри еще",
        "пересмотри",
        "пустой ответ",
        "пусто ответ",
        "не фиксируешь",
        "неверно решил",
        "опять неверно",
        "ты опять",
        "мусор",
        "json в ответе",
    }
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def feedback_signals_enabled() -> bool:
    return _truthy("DIALOGUE_FEEDBACK_SIGNALS_ENABLED", True)


def user_feedback_likely(text: str) -> bool:
    if not feedback_signals_enabled():
        return False
    low = (text or "").strip().lower()
    if len(low) < 4:
        return False
    return any(s in low for s in _FEEDBACK_SUBSTR)


def clip_remark(text: str, max_len: int = 280) -> str:
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def merge_recent_remarks_into_routing_prefs(rp: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    """Вернуть обновлённый routing_prefs (мутация и возврат одного объекта)."""
    if not user_feedback_likely(user_text):
        return rp
    remarks: List[str] = [str(x).strip() for x in (rp.get("recent_user_remarks") or []) if str(x).strip()]
    remark = clip_remark(user_text, 300)
    if remark and (not remarks or remarks[-1] != remark):
        remarks.append(remark)
    rp["recent_user_remarks"] = remarks[-8:]
    return rp


def _feedback_from_meta_intent(meta_intent: Any) -> bool:
    if not isinstance(meta_intent, dict):
        return False
    if str(meta_intent.get("meta") or "") != "user_feedback":
        return False
    try:
        c = float(meta_intent.get("confidence", 0))
    except (TypeError, ValueError):
        c = 0.0
    try:
        floor = max(0.0, min(1.0, float((os.getenv("META_INTENT_MIN_CONFIDENCE") or "0.5").strip() or "0.5")))
    except ValueError:
        floor = 0.5
    return c >= floor


def build_user_remark_hint(
    *,
    user_text: str,
    routing_prefs: Dict[str, Any],
    meta_intent: Any = None,
) -> str:
    """Текст для external_hint (мозг). meta_intent — результат meta_intent_probe (опционально)."""
    if not feedback_signals_enabled():
        return ""
    parts: List[str] = []
    if user_feedback_likely(user_text) or _feedback_from_meta_intent(meta_intent):
        parts.append(
            "(Обратная связь: реплика похожа на поправку к прошлому ответу. "
            "Ответь по сути замечания; не продолжай прежний план или инструменты, если пользователь их отвергает. "
            "При необходимости один короткий уточняющий вопрос.)"
        )
    rp = routing_prefs if isinstance(routing_prefs, dict) else {}
    hist = rp.get("recent_user_remarks")
    if isinstance(hist, list) and hist:
        tail = [str(x).strip() for x in hist[-3:] if str(x).strip()]
        if tail:
            parts.append(
                "(Недавние явные замечания пользователя — учитывай смысл, не пересказывай списком: "
                + " · ".join(tail)
                + ")"
            )
    return "\n".join(parts).strip()
