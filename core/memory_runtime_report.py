"""
Сводка для оператора: что записано в runtime-памяти маршрутов и стратегий.
Файлы: strategy_paths, route_risk, experience_digest; опционально сессия behavior для user_id.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.behavior_store import BehaviorStore
from core.experience_memory import default_store_path as experience_path, experience_enabled
from core.report_timezone import format_operator_datetime_from_iso
from core.route_risk_memory import (
    default_path as route_risk_path,
    route_risk_enabled,
    route_risk_hint_enabled,
)
from core.strategy_path_memory import default_store_path as strategy_path, strategy_path_enabled


def _fmt_ts(value: Any) -> str:
    """Время записи для текста в Telegram: без ISO и без дробных секунд."""
    s = format_operator_datetime_from_iso(value)
    return s if s else "—"


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _tail_jsonl_records(path: str, limit: int) -> List[Dict[str, Any]]:
    if not path or not os.path.isfile(path) or limit <= 0:
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    max_chunk = min(size, 800_000)
    try:
        with open(path, "rb") as f:
            if size > max_chunk:
                f.seek(-max_chunk, 2)
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    out: List[Dict[str, Any]] = []
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
            if len(out) >= limit:
                break
    out.reverse()
    return out


def _format_strategy_rows(rows: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for i, r in enumerate(rows, 1):
        ts = _fmt_ts(r.get("ts"))
        fp = str(r.get("fp") or "")[:12]
        intent = str(r.get("intent") or "")
        mod = str(r.get("module") or "")
        tier = str(r.get("task_tier") or "")
        pstyle = str(r.get("path_style") or "")
        steps = str(r.get("steps_summary") or "").strip()
        ex = str(r.get("assistant_excerpt") or "").strip().replace("\n", " ")
        block = (
            f"{i}) {ts}\n"
            f"   хеш запроса fp…{fp} → intent:{intent} → модуль:{mod}\n"
            f"   уровень задачи: {tier} │ стиль плана: {pstyle}\n"
            f"   план (шаги): {steps or '—'}\n"
            f"   фрагмент ответа: {ex[:280]}{'…' if len(ex) > 280 else ''}"
        )
        lines.append(block)
    return lines


def _format_route_risk_rows(rows: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for i, r in enumerate(rows, 1):
        fp = str(r.get("fp") or "")[:12]
        intent = str(r.get("intent") or "")
        mod = str(r.get("module") or "")
        outc = str(r.get("outcome") or "")
        det = str(r.get("detail") or "")
        ts = str(r.get("ts") or "").strip()
        ts_short = ts[:19] + "…" if len(ts) > 20 else (ts or "—")
        sev = r.get("severity")
        sev_s = f" │ sev:{sev}" if sev is not None else ""
        lines.append(
            f"{i}) ts={ts_short}{sev_s}\n"
            f"   fp…{fp} → intent:{intent} → модуль:{mod}\n"
            f"   исход: {outc} │ деталь: {det or '—'}"
        )
    return lines


def _format_experience_rows(rows: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for i, r in enumerate(rows, 1):
        ts = _fmt_ts(r.get("ts"))
        fp = str(r.get("fp") or "")[:12]
        intent = str(r.get("intent") or "")
        mod = str(r.get("module") or "")
        pr = str(r.get("planner_reason") or "").strip().replace("\n", " ")
        ue = str(r.get("user_excerpt") or "").strip().replace("\n", " ")
        ae = str(r.get("assistant_excerpt") or "").strip().replace("\n", " ")
        lines.append(
            f"{i}) {ts}\n"
            f"   fp…{fp} → intent:{intent} → модуль:{mod}\n"
            f"   маршрутизатор (planner_reason): {pr[:220]}{'…' if len(pr) > 220 else ''}\n"
            f"   запрос: {ue[:180]}{'…' if len(ue) > 180 else ''}\n"
            f"   ответ: {ae[:240]}{'…' if len(ae) > 240 else ''}"
        )
    return lines


def build_memory_insight_payload(
    *,
    limit_per_file: int = 15,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    lim = max(1, min(int(limit_per_file or 15), 80))
    sp = strategy_path()
    rr = route_risk_path()
    ex = experience_path()

    strat_rows = _tail_jsonl_records(sp, lim) if strategy_path_enabled() else []
    risk_rows = _tail_jsonl_records(rr, lim) if route_risk_enabled() else []
    exp_rows = _tail_jsonl_records(ex, lim) if experience_enabled() else []

    behavior_snap: Optional[Dict[str, Any]] = None
    if user_id:
        try:
            rec = BehaviorStore().load(str(user_id), group_id)
            ds = str(rec.get("dialogue_summary") or "").strip()
            st = rec.get("dialogue_state") if isinstance(rec.get("dialogue_state"), dict) else {}
            rp = rec.get("routing_prefs") if isinstance(rec.get("routing_prefs"), dict) else {}
            ea = rec.get("ephemeral_autolearn") if isinstance(rec.get("ephemeral_autolearn"), dict) else {}
            buckets = ea.get("buckets") if isinstance(ea.get("buckets"), dict) else {}
            behavior_snap = {
                "user_id": str(user_id),
                "group_id": group_id,
                "dialogue_summary": ds[:1200],
                "last_intent": str(st.get("last_intent") or ""),
                "task_tier": str(st.get("task_tier") or ""),
                "routing_prefs": rp,
                "ephemeral_autolearn_bucket_count": len(buckets),
            }
        except Exception as e:
            behavior_snap = {"error": str(e)}

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "limits": {"entries_per_file": lim},
        "flags": {
            "STRATEGY_PATH_MEMORY_ENABLED": strategy_path_enabled(),
            "ROUTE_RISK_MEMORY_ENABLED": route_risk_enabled(),
            "ROUTE_RISK_HINT_ENABLED": route_risk_hint_enabled(),
            "EXPERIENCE_MEMORY_ENABLED": experience_enabled(),
            "STRATEGY_PATH_HINT_FOR_SHALLOW": _truthy_env("STRATEGY_PATH_HINT_FOR_SHALLOW", False),
        },
        "paths": {
            "strategy_paths_jsonl": sp,
            "route_risk_jsonl": rr,
            "experience_digest_jsonl": ex,
        },
        "legends": {
            "strategy_paths": (
                "После успешного ответа orchestrator вызывает append_strategy_success: "
                "сохраняются отпечаток запроса (fp), intent, модуль, tier, краткая цепочка шагов lookahead (steps_summary), "
                "фрагмент ответа. Подсказка build_strategy_path_hint подмешивается в контекст мозга при глубоких задачах."
            ),
            "route_risk": (
                "При исходах fallback / clarify / error / failure пишется строка в route_risk.jsonl "
                "(ts, fp, bucket_fp, intent, модуль, outcome, detail, severity). "
                "build_route_risk_hint учитывает TTL (ROUTE_RISK_HINT_TTL_SEC), опционально кластер по началу текста "
                "(ROUTE_RISK_CLUSTER_MATCH), уровни подсказки soft/firm/strong."
            ),
            "experience_digest": (
                "При удачном исходе пишется append_success: planner_reason, отрывки запроса и ответа. "
                "find_hints / build_hint_for_context подмешивают осторожные подсказки при слабом predictive-сигнале."
            ),
            "behavior_session": (
                "Файл сессии пользователя (BehaviorStore): dialogue_summary, routing_prefs, ephemeral_autolearn — "
                "обновляются после реплик в чате."
            ),
        },
        "strategy_paths_tail": strat_rows,
        "route_risk_tail": risk_rows,
        "experience_tail": exp_rows,
        "behavior_session": behavior_snap,
    }


def format_memory_insight_plain(payload: Dict[str, Any]) -> str:
    lim = (payload.get("limits") or {}).get("entries_per_file", "?")
    flags = payload.get("flags") or {}
    paths = payload.get("paths") or {}
    gen = _fmt_ts(payload.get("generated_at"))
    lines: List[str] = [
        "🧠 Память маршрутов и стратегий",
        "",
        f"Обновлено: {gen}",
        f"С хвоста каждого файла: до {lim} записей",
        "",
        "Переключатели",
        f"  strategy_path … {flags.get('STRATEGY_PATH_MEMORY_ENABLED')}",
        f"  route_risk log … {flags.get('ROUTE_RISK_MEMORY_ENABLED')}",
        f"  route_risk hint … {flags.get('ROUTE_RISK_HINT_ENABLED')}",
        f"  experience … {flags.get('EXPERIENCE_MEMORY_ENABLED')}",
        f"  hint_shallow … {flags.get('STRATEGY_PATH_HINT_FOR_SHALLOW')} (подсказка и для shallow)",
        "",
        "Файлы",
        f"  strategy_paths → {paths.get('strategy_paths_jsonl')}",
        f"  route_risk     → {paths.get('route_risk_jsonl')}",
        f"  experience     → {paths.get('experience_digest_jsonl')}",
        "",
        "Как пишется в рантайм",
        "  пользовательский текст",
        "    → нормализация + fp (короткий хеш)",
        "    → планировщик: intent, модуль, tier",
        "    → ответ ассистента",
        "    → при успехе: strategy_paths + experience_digest (и при необходимости другое)",
        "    → при сбое маршрута: route_risk",
        "  подсказки из файлов подмешиваются в контекст перед ответом мозга.",
        "",
    ]

    legends = payload.get("legends") or {}
    lines.append("strategy_paths.jsonl")
    lines.append(str(legends.get("strategy_paths") or ""))
    lines.append("")
    sr = payload.get("strategy_paths_tail") or []
    if not sr:
        lines.append("  (нет записей или память выключена / файл пуст)")
    else:
        lines.extend("  " + x.replace("\n", "\n  ") for x in _format_strategy_rows(sr))
    lines.append("")

    lines.append("route_risk.jsonl")
    lines.append(str(legends.get("route_risk") or ""))
    lines.append("")
    rr = payload.get("route_risk_tail") or []
    if not rr:
        lines.append("  (нет записей или память выключена / файл пуст)")
    else:
        lines.extend("  " + x.replace("\n", "\n  ") for x in _format_route_risk_rows(rr))
    lines.append("")

    lines.append("experience_digest.jsonl")
    lines.append(str(legends.get("experience_digest") or ""))
    lines.append("")
    er = payload.get("experience_tail") or []
    if not er:
        lines.append("  (нет записей или память выключена / файл пуст)")
    else:
        lines.extend("  " + x.replace("\n", "\n  ") for x in _format_experience_rows(er))
    lines.append("")

    beh = payload.get("behavior_session")
    lines.append("Сессия поведения (BehaviorStore)")
    lines.append(str(legends.get("behavior_session") or ""))
    lines.append("")
    if not beh:
        lines.append("  (user_id не передан — только глобальные JSONL выше)")
    elif isinstance(beh, dict) and beh.get("error"):
        lines.append(f"  Ошибка: {beh.get('error')}")
    elif isinstance(beh, dict):
        lines.append(f"  user → {beh.get('user_id')}  group → {beh.get('group_id')}")
        lines.append(f"  last_intent → {beh.get('last_intent')}  tier → {beh.get('task_tier')}")
        lines.append(f"  ephemeral_autolearn buckets → {beh.get('ephemeral_autolearn_bucket_count')}")
        rp = beh.get("routing_prefs") or {}
        if rp:
            lines.append("  routing_prefs:")
            for k, v in list(rp.items())[:12]:
                lines.append(f"    • {k}: {v}")
        ds = str(beh.get("dialogue_summary") or "").strip()
        if ds:
            lines.append("  dialogue_summary:")
            lines.append("    " + ds[:900].replace("\n", " ") + ("…" if len(ds) > 900 else ""))
        else:
            lines.append("  dialogue_summary: (пусто)")
    lines.append("")
    lines.append("Команда: /admin_memory_insight [N] — сколько последних строк из каждого файла (1…80). JSON: /admin_memory_insight_json")
    return "\n".join(lines)


def format_memory_insight_html(payload: Dict[str, Any], *, entry_limit: int = 6) -> str:
    """HTML-отчёт /admin_memory_insight (стиль report_pre_kv)."""
    from core.telegram_ui import esc, report_pre_kv

    lim = (payload.get("limits") or {}).get("entries_per_file", "?")
    flags = payload.get("flags") or {}
    gen = _fmt_ts(payload.get("generated_at"))

    flag_rows = [
        ("strategy_path", "вкл" if flags.get("STRATEGY_PATH_MEMORY_ENABLED") else "выкл"),
        ("route_risk log", "вкл" if flags.get("ROUTE_RISK_MEMORY_ENABLED") else "выкл"),
        ("route_risk hint", "вкл" if flags.get("ROUTE_RISK_HINT_ENABLED") else "выкл"),
        ("experience", "вкл" if flags.get("EXPERIENCE_MEMORY_ENABLED") else "выкл"),
    ]

    lines = [
        "💾 <b>Память маршрутов</b>",
        "",
        "<blockquote><i>Хвост JSONL: strategy_paths, route_risk, experience_digest; "
        "плюс сессия BehaviorStore при user_id.</i></blockquote>",
        "",
        "<blockquote>",
        report_pre_kv(
            [("обновлено", gen or "—"), ("записей/файл", str(lim))] + flag_rows,
            label_max=16,
        ),
        "</blockquote>",
        "",
    ]

    def _section(title: str, rows: List[Dict[str, Any]], fmt_rows) -> None:
        lines.append(f"<b>{title}</b>")
        if not rows:
            lines.append("<blockquote><i>(пусто или память выкл)</i></blockquote>")
        else:
            pre: List[tuple[str, str]] = []
            for i, r in enumerate(rows[-entry_limit:], 1):
                block = fmt_rows([r])
                if block:
                    pre.append((f"#{i}", block[0].replace("\n", " ")[:72]))
            lines.extend(["", "<blockquote>", report_pre_kv(pre, label_max=6, value_max=56), "</blockquote>"])
        lines.append("")

    _section("🛤 strategy_paths", payload.get("strategy_paths_tail") or [], _format_strategy_rows)
    _section("⚠️ route_risk", payload.get("route_risk_tail") or [], _format_route_risk_rows)
    _section("📚 experience", payload.get("experience_tail") or [], _format_experience_rows)

    beh = payload.get("behavior_session")
    lines.append("<b>👤 Сессия</b>")
    if not beh:
        lines.append("<blockquote><i>user_id не задан — только глобальные JSONL</i></blockquote>")
    elif isinstance(beh, dict) and beh.get("error"):
        lines.append(f"<blockquote>⚠️ {esc(str(beh.get('error')))}</blockquote>")
    elif isinstance(beh, dict):
        sess_rows = [
            ("last_intent", str(beh.get("last_intent") or "—")),
            ("task_tier", str(beh.get("task_tier") or "—")),
            ("autolearn buckets", str(beh.get("ephemeral_autolearn_bucket_count") or 0)),
        ]
        ds = str(beh.get("dialogue_summary") or "").strip()
        if ds:
            sess_rows.append(("summary", ds[:200] + ("…" if len(ds) > 200 else "")))
        lines.extend(["", "<blockquote>", report_pre_kv(sess_rows, label_max=14, value_max=48), "</blockquote>"])
    lines.append("")
    lines.append(
        "<blockquote><i>JSON: <code>/admin_memory_insight_json</code> · "
        "лимит: <code>/admin_memory_insight 20</code></i></blockquote>"
    )
    return "\n".join(lines)
