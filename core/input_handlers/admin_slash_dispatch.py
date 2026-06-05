"""
Выполнение admin slash-команд из inline-кнопок (hs:, ha: через _process_message).

Aiogram Command-хендлеры не вызываются для synthetic_payload; оркестратор для admin_*
молча выходит (slash_exclusive). Этот модуль дублирует логику отчётов без второго прохода LLM.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram.types import Message

from core.command_catalog import normalize_command_token
from core.input_handlers.admin_access import admin_guard, effective_admin_user_id
from core.telegram_util import reply_code_plain_chunks, reply_html_chunks, reply_json_chunks


def _args_after_token(text: str, token: str) -> str:
    raw = (text or "").strip()
    parts = raw.split(None, 1)
    if not parts:
        return ""
    first = parts[0].lstrip("/")
    if "@" in first:
        first = first.split("@", 1)[0]
    if first != token:
        return ""
    return (parts[1] if len(parts) > 1 else "").strip()


async def _run_admin_reputation(layer: Any, message: Message, args: str) -> None:
    from core.admin_reputation_view import format_admin_reputation_html, load_admin_reputation_payload

    uid = effective_admin_user_id(message, args)
    payload = load_admin_reputation_payload(uid)
    await reply_html_chunks(message, format_admin_reputation_html(payload))


async def _run_admin_reputation_json(layer: Any, message: Message, args: str) -> None:
    from core.admin_reputation_view import load_admin_reputation_payload

    uid = effective_admin_user_id(message, args)
    payload = load_admin_reputation_payload(uid)
    await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)


async def _run_admin_learning_digest(layer: Any, message: Message, args: str) -> None:
    from core.learning_digest import build_learning_digest, format_learning_digest_html

    uid = effective_admin_user_id(message, args)
    digest = build_learning_digest(user_id=uid)
    await reply_html_chunks(message, format_learning_digest_html(digest))


async def _run_admin_learning_digest_json(layer: Any, message: Message, args: str) -> None:
    from core.learning_digest import build_learning_digest

    uid = effective_admin_user_id(message, args)
    digest = build_learning_digest(user_id=uid)
    await reply_json_chunks(message, digest, ensure_ascii=False, indent=2)


async def _run_admin_route_risk_clusters(layer: Any, message: Message, args: str) -> None:
    from core.admin_route_risk_view import format_route_risk_clusters_html
    from core.route_risk_cluster import cluster_route_risk_recent

    hours = 6.0
    if args:
        try:
            hours = float(args.split()[0])
        except ValueError:
            pass
    pack = cluster_route_risk_recent(hours=hours, min_count=2)
    await reply_html_chunks(message, format_route_risk_clusters_html(pack))


async def _run_admin_route_risk_clusters_json(layer: Any, message: Message, args: str) -> None:
    from core.route_risk_cluster import cluster_route_risk_recent

    hours = 6.0
    if args:
        try:
            hours = float(args.split()[0])
        except ValueError:
            pass
    pack = cluster_route_risk_recent(hours=hours, min_count=2)
    await reply_json_chunks(message, pack, ensure_ascii=False, indent=2)


def _memory_insight_scope(message: Message, args: str) -> tuple[int, Optional[str], Optional[str]]:
    """args: [N] (1…80) и/или user_id; лимит не отменяет actor/from_user."""
    n = 15
    uid: Optional[str] = effective_admin_user_id(message, "") or None
    parts = (args or "").strip().split()
    if parts:
        try:
            candidate = int(parts[0])
            if 1 <= candidate <= 80:
                n = candidate
                if len(parts) > 1:
                    uid = effective_admin_user_id(message, parts[1]) or uid
            else:
                uid = effective_admin_user_id(message, args) or uid
        except ValueError:
            uid = effective_admin_user_id(message, args) or uid
    gid: Optional[str] = None
    if message.chat and message.chat.type in ("group", "supergroup"):
        gid = str(message.chat.id)
    return n, uid, gid


async def _run_admin_memory_insight(layer: Any, message: Message, args: str) -> None:
    from core.memory_runtime_report import build_memory_insight_payload, format_memory_insight_html

    n, uid, gid = _memory_insight_scope(message, args)
    payload = build_memory_insight_payload(limit_per_file=n, user_id=uid, group_id=gid)
    await reply_html_chunks(message, format_memory_insight_html(payload))


async def _run_admin_memory_insight_json(layer: Any, message: Message, args: str) -> None:
    from core.memory_runtime_report import build_memory_insight_payload

    n, uid, gid = _memory_insight_scope(message, args)
    payload = build_memory_insight_payload(limit_per_file=n, user_id=uid, group_id=gid)
    await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)


async def _run_admin_memory_ops(_layer: Any, message: Message, args: str) -> None:
    from core.memory_ops_report import build_memory_ops_report

    turns_n = 25
    memory_n = 5
    if args:
        parts = args.strip().split()
        if parts:
            try:
                turns_n = max(5, min(int(parts[0]), 50))
            except ValueError:
                turns_n = 25
        if len(parts) > 1:
            try:
                memory_n = max(2, min(int(parts[1]), 15))
            except ValueError:
                memory_n = 5
    uid = str(message.from_user.id) if message.from_user else ""
    text = build_memory_ops_report(user_id=uid, turns_limit=turns_n, memory_limit=memory_n)
    await reply_code_plain_chunks(message, text)


async def _run_admin_efficiency(layer: Any, message: Message, args: str) -> None:
    from core.efficiency_report import build_efficiency_snapshot
    from core.telegram_ui import format_efficiency_html

    days = 7.0
    if args:
        try:
            days = float(args.split()[0])
        except (ValueError, IndexError):
            days = 7.0
    payload = build_efficiency_snapshot(days=days, orchestrator=layer.orchestrator)
    await reply_html_chunks(message, format_efficiency_html(payload))


async def _run_admin_mce_status(layer: Any, message: Message, _args: str) -> None:
    from core.admin_mce_status_view import format_mce_status_html
    from core.meta_cognitive_engine import get_mce

    await reply_html_chunks(message, format_mce_status_html(get_mce().snapshot()))


async def _run_admin_mce_status_json(layer: Any, message: Message, _args: str) -> None:
    from core.meta_cognitive_engine import get_mce

    await reply_json_chunks(message, get_mce().snapshot(), ensure_ascii=False, indent=2)


async def _run_admin_autonomy(layer: Any, message: Message, _args: str) -> None:
    from core.admin_autonomy import build_autonomy_report

    rep = build_autonomy_report()
    lines: list[str] = ["<b>🤖 Автономность системы</b>\n"]

    def _val(v: int) -> str:
        return "—" if v < 0 else str(v)

    def _icon(v: int) -> str:
        if v < 0:
            return "❓"
        return "✅" if v > 0 else "·"

    n_lessons = int(rep.get("reflexion_lessons_active", -1))
    lines.append(f"{_icon(n_lessons)} Уроки reflexion: <b>{_val(n_lessons)}</b>")
    n_etalons = int(rep.get("qdrant_etalons_count", -1))
    lines.append(f"{_icon(n_etalons)} Эталонов в Qdrant: <b>{_val(n_etalons)}</b>")
    cls = rep.get("classifier", {}) or {}
    h, m, e = int(cls.get("hits", 0)), int(cls.get("misses", 0)), int(cls.get("errors", 0))
    total = h + m
    hit_pct = round(h / max(total, 1) * 100, 1) if total else 0
    lines.append(f"📊 Классификатор: hits=<b>{h}</b> miss=<b>{m}</b> err=<b>{e}</b> rate=<b>{hit_pct}%</b>")
    cache = rep.get("cache", {}) or {}
    lines.append(
        f"⚡ Cache: coverage=<b>{cache.get('cache_coverage_pct', '?')}%</b> "
        f"hit_rate=<b>{cache.get('hit_rate_pct', '?')}%</b>"
    )
    await reply_html_chunks(message, "\n".join(lines))


async def _run_admin_pulse(layer: Any, message: Message, _args: str) -> None:
    from core.telegram_ui import format_pulse_html

    snap = layer._admin_module.live_pulse_snapshot()
    await reply_html_chunks(message, format_pulse_html(snap))


async def _run_admin_reasoning_quality(layer: Any, message: Message, _args: str) -> None:
    from core.telegram_ui import format_reasoning_quality_html

    payload = layer._admin_module.reasoning_quality_snapshot()
    await reply_html_chunks(message, format_reasoning_quality_html(payload))


async def _run_admin_stats(layer: Any, message: Message, _args: str) -> None:
    await reply_html_chunks(message, layer._admin_module.stats_summary_html())


async def _run_admin_llm_usage(layer: Any, message: Message, args: str) -> None:
    from core.llm_usage_store import aggregate_usage, recent_rows, sorted_records
    from core.monitoring import MONITOR
    from core.telegram_ui import format_llm_usage_html

    opts: Dict[str, Any] = {}
    for part in (args or "").split():
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "days":
            try:
                opts["days"] = float(v)
            except ValueError:
                pass
        elif k == "sort" and v in ("date", "cost", "tokens"):
            opts["sort"] = v
        elif k == "limit":
            try:
                opts["limit"] = int(v)
            except ValueError:
                pass
    days = max(1.0, min(float(opts.get("days", 30.0)), 365.0))
    sort = str(opts.get("sort", "date"))
    limit = int(opts.get("limit", 25))
    agg = aggregate_usage(days=days)
    window_rows = recent_rows(days=days)
    top = sorted_records(window_rows, sort=sort, limit=limit)
    nanos = int(MONITOR.counters.get("openrouter_cost_credits_nanos_total", 0))
    html_out = format_llm_usage_html(
        agg,
        session_cost_usd=nanos / 1e9,
        top_rows=top,
        sort_label=sort,
    )
    await reply_html_chunks(message, html_out)


_DISPATCH: Dict[str, Callable[[Any, Message, str], Awaitable[None]]] = {
    "admin_reputation": _run_admin_reputation,
    "admin_reputation_json": _run_admin_reputation_json,
    "admin_learning_digest": _run_admin_learning_digest,
    "admin_learning_digest_json": _run_admin_learning_digest_json,
    "admin_route_risk_clusters": _run_admin_route_risk_clusters,
    "admin_route_risk_clusters_json": _run_admin_route_risk_clusters_json,
    "admin_memory_insight": _run_admin_memory_insight,
    "admin_memory_insight_json": _run_admin_memory_insight_json,
    "admin_memory_ops": _run_admin_memory_ops,
    "admin_efficiency": _run_admin_efficiency,
    "admin_mce_status": _run_admin_mce_status,
    "admin_mce_status_json": _run_admin_mce_status_json,
    "admin_autonomy": _run_admin_autonomy,
    "admin_pulse": _run_admin_pulse,
    "admin_reasoning_quality": _run_admin_reasoning_quality,
    "admin_stats": _run_admin_stats,
    "admin_llm_usage": _run_admin_llm_usage,
}


async def try_dispatch_admin_slash(layer: Any, message: Message, text: str) -> bool:
    """True если команда обработана (admin_* из HELP_STATS_ACTIONS и родственные)."""
    tok = normalize_command_token(text)
    if not tok or tok not in _DISPATCH:
        return False
    if not await admin_guard(message, layer):
        return False
    args = _args_after_token(text, tok)
    await _DISPATCH[tok](layer, message, args)
    return True
