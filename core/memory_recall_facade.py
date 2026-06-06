"""
Единая точка детерминированного recall: pipeline (external_hint), slash /dialog_recall, опционально LLM-сжатие в модуле.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.dialogue_lookups import build_relative_time_archive_hint_for_llm
from core.self_model import autonomy_extended_enabled


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_i(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def _env_f(name: str, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def auto_memory_recall_enabled() -> bool:
    """Автопакет «фона» (архив+digest+summary) при тонком recent_dialogue и маркерах в тексте."""
    return _truthy("MEMORY_RECALL_AUTO_ENABLED", False)


def _recent_dialogue_message_count(recent_dialogue: Any) -> int:
    if not isinstance(recent_dialogue, list):
        return 0
    n = 0
    for row in recent_dialogue:
        if isinstance(row, dict) and str(row.get("text") or "").strip():
            n += 1
    return n


def nl_dialog_recall_route_enabled() -> bool:
    """По фразам без slash направить диалог в dialog_memory_recall (как /dialog_recall summary)."""
    return _truthy("DIALOG_RECALL_NL_ROUTE_ENABLED", False)


def session_meta_recall_enabled() -> bool:
    """Детерминированный ответ «первое сообщение / темы» до LLM (pre_llm_plan)."""
    return _truthy("SESSION_META_RECALL_ENABLED", True)


_SESSION_META_MARKERS = (
    "первое сообщен",
    "первое моё сообщен",
    "первое мое сообщен",
    "первое что",
    "самое первое",
    "с чего начал",
    "начало диалога",
    "начало нашего",
    "начало переписк",
    "темы разговора",
    "тема разговора",
    "какие темы",
    "о чём мы говорили",
    "о чем мы говорили",
    "что ты помнишь",
    "что помнишь из",
    "что помнишь о",
    "напиши первое",
    "first message",
    "conversation topics",
)


def _text_matches_session_meta_markers(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 10 or t.lstrip().startswith("/"):
        return False
    low = t.lower()
    return any(m in low for m in _SESSION_META_MARKERS)


def plain_text_requests_session_meta_recall(text: str) -> bool:
    """
    «Первое сообщение», «темы разговора» — без угадывания LLM по recent_messages.
    Не требует DIALOG_RECALL_NL_ROUTE_ENABLED.
    """
    if not session_meta_recall_enabled():
        return False
    return _text_matches_session_meta_markers(text)


def user_text_needs_dialogue_archive_context(text: str) -> bool:
    """
    Вопрос про сохранённую переписку (архив/summary), не про календарь «вчера».
    Используется в pipeline external_hint — даже если pre_llm не сработал.
    """
    return _text_matches_session_meta_markers(text)


def _first_user_text_in_archive(items: List[Dict[str, Any]]) -> str:
    for m in items:
        if str(m.get("role") or "").strip().lower() != "user":
            continue
        t = str(m.get("text") or "").strip()
        if t:
            return t
    return ""


def _topic_hints_from_archive(
    items: List[Dict[str, Any]],
    *,
    max_topics: int = 8,
    line_max: int = 120,
) -> List[str]:
    from core.dialog_memory_recall import _clip

    user_texts = [
        str(m.get("text") or "").strip()
        for m in items
        if str(m.get("role") or "").strip().lower() == "user"
        and str(m.get("text") or "").strip()
    ]
    if not user_texts:
        return []
    n = len(user_texts)
    if n <= max_topics:
        indices = list(range(n))
    else:
        indices = sorted(
            {
                0,
                1,
                2,
                n // 4,
                n // 2,
                (3 * n) // 4,
                max(0, n - 3),
                max(0, n - 2),
                n - 1,
            }
        )
    seen: set[str] = set()
    out: List[str] = []
    for i in indices:
        if i < 0 or i >= n:
            continue
        raw = user_texts[i]
        key = raw.lower()[:72]
        if key in seen:
            continue
        seen.add(key)
        out.append(_clip(raw, line_max))
        if len(out) >= max_topics:
            break
    return out


def build_session_meta_recall_reply(
    *,
    user_id: str,
    group_id: Optional[str],
    context: Dict[str, Any],
) -> str:
    """
    Факты из архива и session_first_user_text; без выдумки «Привет» и без смешения ботов.
    """
    from core.dialog_memory_recall import _clip, format_archive_summary
    from core.message_archive import load_message_archive_items

    uid = (user_id or "").strip()
    if not uid:
        return "(Нет user_id для recall.)"

    sess_first = ""
    dsum = ""
    if isinstance(context, dict):
        sess_first = str(context.get("session_first_user_text") or "").strip()
        dsum = str(context.get("dialogue_summary") or "").strip()

    try:
        items = load_message_archive_items(uid, group_id)
    except Exception:
        items = []

    archive_first = _first_user_text_in_archive(items)
    lines: List[str] = [
        "По сохранённой переписке с этим ботом (не угадываю по короткому окну recent_messages):",
        "",
    ]

    if archive_first:
        lines.append(f"• Первое ваше сообщение в архиве (с самого начала): «{_clip(archive_first, 500)}»")
    else:
        lines.append("• Первое сообщение в архиве: (архив пуст или отключён)")

    if sess_first:
        if archive_first and sess_first.strip().lower() == archive_first.strip().lower():
            lines.append(
                "• Текущая сессия: с последнего сброса epoch первое совпадает с архивом."
            )
        else:
            lines.append(
                f"• Первое в текущей сессии (после сброса epoch, поле session_first_user_text): «{_clip(sess_first, 500)}»"
            )
    else:
        lines.append("• Текущая сессия: session_first_user_text пока не задан.")

    topics = _topic_hints_from_archive(items)
    if topics:
        lines.append("")
        lines.append("Основные темы (выборка ваших реплик по архиву, без LLM):")
        for t in topics:
            lines.append(f"  — {t}")

    if dsum:
        lines.append("")
        lines.append(f"Сжатое summary: {_clip(dsum, 720)}")

    arch_tail = format_archive_summary(items, tail_n=_env_i("SESSION_META_RECALL_TAIL", 12, 4, 40))
    if arch_tail:
        lines.append("")
        lines.append(arch_tail)

    lines.append("")
    lines.append(
        "Память у каждого бота (Example Bot / VPNTEST) своя; на другом инстансе цифры могут отличаться."
    )
    return "\n".join(lines).strip()


def plain_text_requests_dialog_recall(text: str) -> bool:
    """
    Узнавание запроса «покажи переписку / напомни что обсуждали» без команды.
    Только при DIALOG_RECALL_NL_ROUTE_ENABLED=true.
    """
    if not nl_dialog_recall_route_enabled():
        return False
    t = (text or "").strip()
    if len(t) < 8 or t.lstrip().startswith("/"):
        return False
    low = t.lower()
    markers = (
        "напомни переписк",
        "напомни что писали",
        "что писали в чате",
        "что в переписке",
        "покажи переписк",
        "история чата",
        "что обсуждали",
        "что мы обсуждали",
        "напомни что обсуждали",
        "что мы говорили",
        "пролистай переписк",
        "scroll back",
        "conversation history",
        "what did we discuss",
        "remind me what we",
        "покажи историю",
        "историю переписк",
    )
    return any(m in low for m in markers)


def user_text_suggests_broad_context_recall(text: str) -> bool:
    """
    Продолжение темы без явной календарной привязки (относительный recall обрабатывается отдельно).
    """
    t = (text or "").strip().lower()
    if len(t) < 4:
        return False
    markers = (
        "продолж",
        "как раньше",
        "сделай как",
        "то же самое",
        "тоже самое",
        "с того места",
        "с этого места",
        "pick up where",
        "continue where",
        "as before",
        "same as",
    )
    return any(m in t for m in markers)


def self_model_suggests_memory_boost(context: Dict[str, Any]) -> bool:
    """
    Когда autonomy extended включён и в dynamic высокий clarify_rate или низкая context_stability —
    имеет смысл подмешать тот же «тонкий» пакет recall (см. MEMORY_RECALL_SELF_MODEL_BOOST_*).
    """
    if not _truthy("MEMORY_RECALL_SELF_MODEL_BOOST_ENABLED", False):
        return False
    if not autonomy_extended_enabled():
        return False
    sm = context.get("self_model") if isinstance(context, dict) else None
    if not isinstance(sm, dict):
        return False
    dyn = sm.get("dynamic")
    if not isinstance(dyn, dict):
        return False
    try:
        cr = float(dyn.get("clarify_rate", 0.0))
    except (TypeError, ValueError):
        cr = 0.0
    try:
        cs = float(dyn.get("context_stability", 1.0))
    except (TypeError, ValueError):
        cs = 1.0
    thr_cr = _env_f("MEMORY_RECALL_SELF_MODEL_CLARIFY_RATE_MIN", 0.35, 0.0, 1.0)
    thr_cs = _env_f("MEMORY_RECALL_SELF_MODEL_CONTEXT_STAB_MAX", 0.55, 0.0, 1.0)
    return cr >= thr_cr or cs <= thr_cs


def _chunks_include_facade_thin_pack(chunks: List[str]) -> bool:
    return any((c or "").strip().startswith("(MemoryRecallFacade)") for c in chunks)


def _build_facade_thin_pack_chunk(
    *,
    uid: str,
    group_id: Optional[str],
    context: Dict[str, Any],
    recent_dialogue: Any,
) -> Optional[str]:
    max_recent = _env_i("MEMORY_RECALL_AUTO_MAX_RECENT_MESSAGES", 8, 2, 24)
    if _recent_dialogue_message_count(recent_dialogue) > max_recent:
        return None
    from core.dialog_memory_recall import (
        format_archive_summary,
        format_dialogue_summary_block,
        read_session_digest_for_user,
    )
    from core.message_archive import load_message_archive_items

    try:
        items = load_message_archive_items(uid, group_id)
    except Exception:
        items = []
    tail = _env_i("MEMORY_RECALL_AUTO_ARCHIVE_TAIL", 20, 6, 40)
    digest_max = _env_i("MEMORY_RECALL_DIGEST_MAX", 4, 1, 12)

    parts: List[str] = []
    dsum = str((context or {}).get("dialogue_summary") or "")
    ds = format_dialogue_summary_block(dsum, max_chars=720)
    if ds:
        parts.append(ds)
    dig = read_session_digest_for_user(str(uid), max_records=digest_max)
    if dig:
        parts.append(dig)
    arch_s = format_archive_summary(items, tail_n=tail)
    if arch_s:
        parts.append(arch_s)
    if not parts:
        return None
    return (
        "(MemoryRecallFacade) Короткий фон из памяти при «тонком» recent_dialogue "
        f"(≤{max_recent} реплик с текстом). Используй как опору, не выдумывай сверх неё.\n\n"
        + "\n\n".join(parts)
    )


def build_pipeline_memory_addon(
    *,
    user_text: str,
    user_id: Optional[str],
    group_id: Optional[str],
    context: Dict[str, Any],
    recent_dialogue: Any,
    user_facts: Dict[str, Any],
    telegram_message_unix: Optional[int],
    need_memory: bool = False,
) -> str:
    """
    Фрагмент для external_hint: окно «вчера/неделю назад»; при запросе памяти — архив/summary.
    """
    chunks: List[str] = []
    ctx = context if isinstance(context, dict) else {}

    rel = build_relative_time_archive_hint_for_llm(
        user_text,
        user_id=user_id,
        group_id=group_id,
        user_facts=user_facts,
        recent_messages=recent_dialogue if isinstance(recent_dialogue, list) else None,
        telegram_message_unix=telegram_message_unix,
    )
    if (rel or "").strip():
        chunks.append(rel.strip())

    uid = (user_id or "").strip()

    if uid and user_text_needs_dialogue_archive_context(user_text) and not (rel or "").strip():
        meta = build_session_meta_recall_reply(
            user_id=uid,
            group_id=group_id,
            context={
                "session_first_user_text": str(
                    ctx.get("session_first_user_text") or ""
                ).strip(),
                "dialogue_summary": str(ctx.get("dialogue_summary") or "").strip(),
            },
        )
        if meta.strip():
            chunks.append(
                "(MemoryRecallFacade) Запрос про память диалога — опирайся на факты ниже, "
                "не выдумывай первое сообщение и темы.\n\n" + meta.strip()
            )

    if (
        auto_memory_recall_enabled()
        and uid
        and user_text_suggests_broad_context_recall(user_text)
        and not (rel or "").strip()
        and not _chunks_include_facade_thin_pack(chunks)
    ):
        thin = _build_facade_thin_pack_chunk(
            uid=uid, group_id=group_id, context=context, recent_dialogue=recent_dialogue
        )
        if thin:
            chunks.append(thin)

    if (
        need_memory
        and uid
        and not (rel or "").strip()
        and not _chunks_include_facade_thin_pack(chunks)
    ):
        thin_nm = _build_facade_thin_pack_chunk(
            uid=uid, group_id=group_id, context=context, recent_dialogue=recent_dialogue
        )
        if thin_nm:
            chunks.append(thin_nm)

    if (
        uid
        and not (rel or "").strip()
        and not _chunks_include_facade_thin_pack(chunks)
        and self_model_suggests_memory_boost(context)
    ):
        thin2 = _build_facade_thin_pack_chunk(
            uid=uid, group_id=group_id, context=context, recent_dialogue=recent_dialogue
        )
        if thin2:
            chunks.append(thin2)

    return "\n\n".join(chunks).strip()


def build_slash_recall_bundle(
    *,
    user_id: str,
    group_id: Optional[str],
    context: Dict[str, Any],
    mode: str,
    query: str = "",
    archive_tail: int = 28,
    window_pick: Optional[str] = None,
) -> str:
    """
    Тот же детерминированный набор, что и для /dialog_recall (summary | archive | search | когда).
    Форматтеры остаются в core.dialog_memory_recall (избегаем дублирования логики клипа/архива).
    """
    from core import dialog_memory_recall as dmr
    from core.message_archive import load_message_archive_items
    from core.relative_dialogue_time import (
        filter_archive_items_by_unix_window,
        format_relative_window_hint_lines,
        merge_archive_and_recent_ts,
        parse_recall_time_window_unix,
        recall_query_wants_earliest,
    )

    blocks: List[str] = []
    mode_l = (mode or "summary").strip().lower()
    q = (query or "").strip()

    if mode_l == "search" and not q:
        return "Укажи строку поиска: /dialog_recall search твоя_фраза"

    items: List[Dict[str, Any]] = load_message_archive_items(str(user_id), group_id) if user_id else []

    mem0 = context.get("mem0_facts") if isinstance(context, dict) else None

    if mode_l in ("when", "когда"):
        if not q:
            return (
                "Укажи период: /dialog_recall когда вчера утром\n"
                "Примеры: неделю назад, 3 дня назад вечером, позавчера днём."
            )
        facts = context.get("user_facts") if isinstance(context, dict) else {}
        if not isinstance(facts, dict):
            facts = {}
        tgux = context.get("telegram_message_date_unix") if isinstance(context, dict) else None
        try:
            ref_ts = int(tgux) if tgux is not None else None
        except (TypeError, ValueError):
            ref_ts = None
        ref = (
            datetime.fromtimestamp(ref_ts, tz=timezone.utc)
            if ref_ts is not None
            else datetime.now(timezone.utc)
        )
        parsed = parse_recall_time_window_unix(q, user_facts=facts, reference_utc=ref)
        if not parsed:
            return (
                "Не разобрал период. Примеры: вчера утром, 40 дней назад, в апреле, 2026-03-30, неделю назад. "
                "Для «утро/день/вечер» задай timezone в профиле."
            )
        start_u, end_u, label = parsed
        recent = context.get("recent_dialogue") if isinstance(context, dict) else None
        if recent is None and isinstance(context, dict):
            recent = context.get("recent_messages")
        merged = merge_archive_and_recent_ts(items, recent if isinstance(recent, list) else [])
        wp = (window_pick or "").strip().lower()
        if wp in ("earliest", "first", "asc"):
            newest_first = False
        elif wp in ("latest", "last", "desc", "newest"):
            newest_first = True
        else:
            newest_first = not recall_query_wants_earliest(q)
        rows = filter_archive_items_by_unix_window(
            merged, start_u, end_u, newest_first=newest_first
        )
        parts: List[str] = []
        m0w = dmr.format_mem0_for_recall(mem0)
        if m0w:
            parts.append(m0w)
        parts.append(
            format_relative_window_hint_lines(
                rows, label=label, picked_earliest=not newest_first
            )
        )
        return "\n\n".join(parts).strip()

    m0 = dmr.format_mem0_for_recall(mem0)
    if m0:
        blocks.append(m0)

    dsum = context.get("dialogue_summary") if isinstance(context, dict) else ""
    ds = dmr.format_dialogue_summary_block(str(dsum or ""))
    if ds and mode_l in ("summary",):
        blocks.append(ds)

    dig = dmr.read_session_digest_for_user(
        str(user_id), max_records=_env_i("DIALOG_RECALL_DIGEST_MAX", 5, 1, 20)
    )
    if dig and mode_l == "summary":
        blocks.append(dig)

    tail_n = archive_tail if mode_l != "search" else _env_i("DIALOG_RECALL_SEARCH_WINDOW", 80, 10, 200)
    line_max = _env_i("DIALOG_RECALL_LINE_MAX", 220, 80, 400)
    if mode_l == "summary":
        blocks.append(dmr.format_archive_summary(items, tail_n=tail_n))
    elif mode_l == "archive":
        blocks.append(dmr.format_archive_for_recall(items, tail_n=tail_n, query=None, line_max=line_max))
    elif mode_l == "search":
        blocks.append(dmr.format_archive_for_recall(items, tail_n=tail_n, query=q, line_max=line_max))
    else:
        blocks.append("(Неизвестный режим; используй summary, archive или search.)")

    return "\n\n".join(b for b in blocks if b).strip() or "(Нечего показать.)"
