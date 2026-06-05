"""
Эвристическое накопление «цифрового следа» пользователя для ассистента:
счётчики по ходам, короткие теги привычек и текстовый блок «как система видит пользователя».

Не NLP-классификация и не клинический профиль — только сигналы из BehaviorStore и session_task.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_DISCLAIMER_RU = (
    "Сводка эвристическая: по длине сообщений, маршрутам, инструментам и эвристике /psych; "
    "не оценка личности, не медицинский или юридический вывод."
)


def user_agent_impression_enabled() -> bool:
    raw = os.getenv("USER_AGENT_IMPRESSION_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_mod(s: str) -> str:
    return str(s or "").strip().lower()


def _bump(d: Dict[str, Any], key: str, n: int = 1) -> None:
    try:
        d[key] = int(d.get(key) or 0) + n
    except (TypeError, ValueError):
        d[key] = n


def _cap_list(items: List[str], max_n: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        t = str(x).strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
        if len(out) >= max_n:
            break
    return out


def _trait_from_module(mod: str) -> Optional[str]:
    m = _norm_mod(mod)
    if not m:
        return None
    if "law" in m or "legal" in m:
        return "частые обращения к правовым инструментам"
    if "document" in m or "corpus" in m:
        return "работа с документами и корпусом"
    if "goal_runner" in m or "goal" == m:
        return "многошаговые сценарии (goal runner)"
    if "math" in m or "arithmetic" in m:
        return "расчёты и формулы"
    if "wiki" in m:
        return "справочные запросы (вики)"
    return None


def _trait_from_tool(tool: str) -> Optional[str]:
    t = _norm_mod(tool)
    if not t:
        return None
    if t.startswith("lawsearch"):
        return "поиск и загрузка НПА"
    if t.startswith("documentcorpus"):
        return "поиск по локальному корпусу"
    if t.startswith("userknowledge"):
        return "личный архив и заметки"
    if t.startswith("urlfetch") or t.startswith("universalsearch"):
        return "проверка фактов в сети"
    return None


def _rebuild_summary_ru(
    counters: Dict[str, Any],
    traits: List[str],
    conversation_style: str,
    psych: Optional[Dict[str, Any]],
) -> str:
    turns = max(1, int(counters.get("turns_recorded") or 1))
    chunks: List[str] = []

    law_share = float(counters.get("module_law_adj", 0)) / float(turns)
    doc_share = float(counters.get("module_doc_adj", 0)) / float(turns)
    tool_n = int(counters.get("tool_uses", 0) or 0)

    if law_share >= 0.2:
        chunks.append("Заметна тематика законов и официальных актов.")
    elif int(counters.get("module_law_adj", 0) or 0) >= 3:
        chunks.append("Были повторные запросы к правовым инструментам.")

    if doc_share >= 0.15:
        chunks.append("Часто используются документы, RAG или корпус.")

    if tool_n >= 5 and tool_n / float(turns) >= 0.4:
        chunks.append("Диалог опирается на инструменты (поиск, реестры, файлы) чаще обычного.")

    long_r = float(counters.get("messages_long", 0) or 0) / float(turns)
    short_r = float(counters.get("messages_short", 0) or 0) / float(turns)
    if long_r >= 0.35:
        chunks.append("Сообщения часто развёрнутые — вероятно нужны подробные ответы.")
    elif short_r >= 0.45:
        chunks.append("Много коротких реплик — возможно удобен лаконичный стиль.")

    cs = _norm_mod(conversation_style)
    if cs and cs != "balanced":
        chunks.append(f"Выбран режим общения в чате: «{cs}».")

    if int(counters.get("admin_turns", 0) or 0) >= 2:
        chunks.append("Есть обращения с админ-контекстом Telegram.")

    if isinstance(psych, dict):
        streak = int(psych.get("stress_streak") or 0)
        la = psych.get("last_analysis") if isinstance(psych.get("last_analysis"), dict) else {}
        if streak >= 3 or (isinstance(la, dict) and la.get("sentiment") == "stressed"):
            chunks.append("Эвристика тона (/psych) отмечает признаки стресса или усталости в формулировках.")

    if traits:
        tail = ", ".join(traits[:5])
        chunks.append(f"Наблюдаемые привычки: {tail}.")

    if not chunks:
        return "Пока мало устойчивых сигналов по переписке; выводы уточнятся со временем."

    return " ".join(chunks)


def update_user_agent_impression_in_record(
    rec: Dict[str, Any],
    *,
    user_id: str,
    user_text: str,
    telegram_is_admin: bool = False,
) -> None:
    """
    Обновляет rec['user_agent_impression'] на основе текущего хода и session_task.
    Вызывать перед финальным behavior_store.save после хода.
    """
    if not user_agent_impression_enabled() or not isinstance(rec, dict):
        return

    st = rec.get("session_task") if isinstance(rec.get("session_task"), dict) else {}
    last_mod = _norm_mod(str(st.get("last_module") or ""))
    last_tool = _norm_mod(str(st.get("last_tool") or ""))
    tool_ok = st.get("last_tool_ok")

    base = rec.get("user_agent_impression")
    if not isinstance(base, dict):
        base = {}
    counters: Dict[str, Any] = dict(base.get("counters") or {}) if isinstance(base.get("counters"), dict) else {}
    _bump(counters, "turns_recorded", 1)

    ut = (user_text or "").strip()
    if len(ut) > 400:
        _bump(counters, "messages_long", 1)
    elif len(ut) > 0 and len(ut) < 36:
        _bump(counters, "messages_short", 1)

    if telegram_is_admin:
        _bump(counters, "admin_turns", 1)

    if last_tool:
        _bump(counters, "tool_uses", 1)
        if tool_ok is False:
            _bump(counters, "tool_failures", 1)

    if "law" in last_mod or "legal" in last_mod:
        _bump(counters, "module_law_adj", 1)
    if "document" in last_mod or "corpus" in last_mod:
        _bump(counters, "module_doc_adj", 1)

    old_habits: List[str] = list(base.get("habit_tags") or []) if isinstance(base.get("habit_tags"), list) else []
    new_bits: List[str] = []
    hit_m = _trait_from_module(last_mod)
    if hit_m:
        new_bits.append(hit_m)
    hit_t = _trait_from_tool(last_tool)
    if hit_t:
        new_bits.append(hit_t)
    habits = _cap_list(old_habits + new_bits, 24)
    traits = list(habits[:18])

    psych: Optional[Dict[str, Any]] = None
    uid = str(user_id or "").strip()
    if uid:
        try:
            from core.psychology_engine import PsychologyEngineModule

            psych = PsychologyEngineModule().get_psychology_profile(uid)
        except Exception:
            psych = None

    conv_style = str(rec.get("conversation_style") or "").strip()
    summary = _rebuild_summary_ru(counters, traits, conv_style, psych)

    rec["user_agent_impression"] = {
        "version": 1,
        "counters": counters,
        "habit_tags": habits[-20:],
        "traits": traits,
        "assistant_view": {
            "summary_ru": summary,
            "disclaimer_ru": _DISCLAIMER_RU,
        },
        "last_updated": _utc_iso(),
    }


def impression_excerpt_for_snapshot(rec: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Фрагмент для DigitalTwin.user_snapshot_for_agent без повторной записи."""
    imp = rec.get("user_agent_impression")
    if not isinstance(imp, dict) or not imp:
        return {}, (
            "Профиль привычек ещё не накоплен (мало ходов или USER_AGENT_IMPRESSION_ENABLED=off)."
        )
    av = imp.get("assistant_view") if isinstance(imp.get("assistant_view"), dict) else {}
    excerpt = {
        "counters": imp.get("counters"),
        "habit_tags": (imp.get("habit_tags") or [])[:16],
        "traits": (imp.get("traits") or [])[:14],
        "assistant_view_ru": {
            "summary": str(av.get("summary_ru") or "").strip(),
            "disclaimer": str(av.get("disclaimer_ru") or _DISCLAIMER_RU).strip(),
        },
        "last_updated": imp.get("last_updated"),
    }
    hint = (
        "assistant_view_ru.summary — эвристика «что система заметила о пользователе»; "
        "не путать с agent self_model (уверенность маршрутизатора)."
    )
    return excerpt, hint
