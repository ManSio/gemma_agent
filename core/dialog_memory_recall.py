"""
Детерминированный «пересказ» для slash /dialog_recall: архив реплик, digest сессии, сжатие без LLM.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_WS = re.compile(r"\s+")


def _ts_label(raw: Any) -> str:
    if raw is None:
        return ""
    try:
        ts = int(raw)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, ValueError, TypeError, OverflowError):
        return ""


def _clip(s: str, n: int) -> str:
    t = _WS.sub(" ", (s or "").strip())
    if len(t) <= n:
        return t
    return t[: max(0, n - 1)] + "…"


def format_mem0_for_recall(facts: Any, *, max_items: int = 12, line_max: int = 220) -> str:
    if not isinstance(facts, list) or not facts:
        return ""
    lines: List[str] = []
    for item in facts[-max_items:]:
        if isinstance(item, dict):
            blob = str(
                item.get("memory")
                or item.get("content")
                or item.get("text")
                or item.get("data")
                or ""
            ).strip()
        elif isinstance(item, str):
            blob = item.strip()
        else:
            blob = str(item).strip()
        if blob:
            lines.append(_clip(blob, line_max))
    if not lines:
        return ""
    return "Mem0 (факты из поиска по текущему запросу):\n" + "\n".join(f"• {x}" for x in lines)


def format_dialogue_summary_block(summary: str, *, max_chars: int = 900) -> str:
    s = (summary or "").strip()
    if not s:
        return ""
    return "Сжатый dialogue_summary (из BehaviorStore):\n" + _clip(s, max_chars)


def format_archive_for_recall(
    items: List[Dict[str, Any]],
    *,
    tail_n: int,
    query: Optional[str] = None,
    line_max: int = 200,
) -> str:
    if not items:
        return "(Архив сообщений пуст или отключён DIALOGUE_MESSAGE_ARCHIVE_ENABLED.)"
    q = (query or "").strip().lower()
    slice_items = items[-max(1, tail_n) :]
    if q:
        slice_items = [m for m in slice_items if q in str(m.get("text") or "").lower()]
        if not slice_items:
            return f"(В последних {tail_n} репликах архива нет совпадений с «{query[:80]}».)"
    lines: List[str] = [f"Архив переписки (хвост, до {len(slice_items)} реплик):"]
    for m in slice_items:
        role = str(m.get("role") or "?").strip()
        ts_l = _ts_label(m.get("telegram_ts"))
        prefix = f"[{ts_l}] " if ts_l else ""
        body = _clip(str(m.get("text") or ""), line_max)
        if body:
            lines.append(f"{prefix}{role}: {body}")
    return "\n".join(lines)


def format_archive_summary(items: List[Dict[str, Any]], *, tail_n: int = 24) -> str:
    """Короткий пересказ: только усечённые реплики по ролям."""
    if not items:
        return ""
    tail = items[-max(4, tail_n) :]
    lines: List[str] = ["Краткий хвост архива (для ориентира):"]
    for m in tail:
        role = str(m.get("role") or "?").strip()[:1].upper()
        ts_l = _ts_label(m.get("telegram_ts"))
        p = f"{ts_l} " if ts_l else ""
        lines.append(f"- {p}{role}: {_clip(str(m.get('text') or ''), 140)}")
    return "\n".join(lines)


def read_session_digest_for_user(user_id: str, *, max_records: int = 5) -> str:
    uid = (user_id or "").strip()
    if not uid:
        return ""
    try:
        from core.session_digest import default_path, digest_enabled
    except Exception:
        return ""
    if not digest_enabled():
        return ""
    path = default_path()
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return ""
    hits: List[Dict[str, Any]] = []
    for line in reversed(all_lines):
        line = (line or "").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and str(obj.get("user_id") or "").strip() == uid:
            hits.append(obj)
            if len(hits) >= max_records:
                break
    if not hits:
        return ""
    out: List[str] = ["Session digest (последние записи JSONL для этого user_id):"]
    for h in hits:
        ts = str(h.get("ts") or "")[:22]
        turns = h.get("turns")
        out.append(
            f"• {ts} turns={turns} ok={h.get('ok')} clarify={h.get('clarify')} "
            f"fail={h.get('failure')} fb={h.get('fallback')}"
        )
        samples = h.get("samples") if isinstance(h.get("samples"), list) else []
        for s in samples[:4]:
            if not isinstance(s, dict):
                continue
            ex = str(s.get("user_excerpt") or "").strip()
            if ex:
                out.append(f"  — {_clip(ex, 120)}")
    return "\n".join(out)


def build_recall_bundle_text(
    *,
    user_id: str,
    group_id: Optional[str],
    context: Dict[str, Any],
    mode: str,
    query: str = "",
    archive_tail: int = 28,
) -> str:
    """Делегирует в memory_recall_facade.build_slash_recall_bundle (единая сборка)."""
    from core.memory_recall_facade import build_slash_recall_bundle

    return build_slash_recall_bundle(
        user_id=user_id,
        group_id=group_id,
        context=context,
        mode=mode,
        query=query,
        archive_tail=archive_tail,
    )


def recall_help_text() -> str:
    return (
        "/dialog_recall — краткий отчёт из долговременного архива реплик, digest сессии и Mem0.\n"
        "• /dialog_recall или /dialog_recall summary — сводка: Mem0, summary, digest, хвост архива.\n"
        "• /dialog_recall archive [N] — последние N реплик из архива с метками времени (если есть).\n"
        "• /dialog_recall search <фраза> — поиск фразы в хвосте архива.\n"
        "• /dialog_recall когда <период> — окно: вчера утром, 40 дней назад, в апреле, 2026-03-30; «первая запись» — с начала окна.\n"
        "• /dialog_recall llm [режим] — тот же сбор фактов, затем сжатие в связный текст через мозг (DIALOG_RECALL_LLM_ENABLED).\n"
        "  Пример: /dialog_recall llm когда вчера утром\n"
        "Без slash: при DIALOG_RECALL_NL_ROUTE_ENABLED=true фразы вроде «напомни переписку» дают тот же отчёт, что summary.\n"
        "Память сообщений: data/behavior/message_archive/ (см. DIALOGUE_MESSAGE_ARCHIVE_*)."
    )
