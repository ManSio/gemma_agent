from __future__ import annotations

import logging

import os
from typing import Any, Dict, List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from core.diagnostics import build_diagnostic_snapshot
from core.data_governance import DG
from core.error_analysis import read_recent_events, runtime_errors_file_meta
from core.recovery_autonomy import build_unified_health_snapshot
from core.development_passport import get_development_passport, get_passport_source_info
from core.report_i18n import (
    RUNTIME_SEVERITY_RU,
    format_top_counters_chart_html,
    ru_bool,
    ru_status,
    runtime_component_label_ru,
)
from core.report_timezone import format_operator_datetime_from_iso
from core.telegram_ui import (
    code_block_html,
    esc,
    format_errors_compact_html,
    format_governance_html,
    format_health_short_html,
    format_anti_flood_html,
    format_pulse_html,
    format_xray_html,
    report_pre_kv,
)
from core.config_manager import get_config
from core.mem0_memory.mem0_module import mem0_operator_diagnostics
from core.runtime_telegram_settings import TOGGLE_DEFS, effective_bool, snapshot_for_operator
from core.operator_rules import snapshot_for_operator as operator_rules_snapshot
from core.ephemeral_lessons import snapshot_for_operator as ephemeral_lessons_snapshot
from core.ephemeral_autolearn import snapshot_for_operator as ephemeral_autolearn_snapshot
from core.voice_module import VoiceModule


logger = logging.getLogger(__name__)

def _format_runtime_error_lines(
    rows: List[Dict[str, Any]],
    *,
    component_filter: Optional[str] = None,
    newest_first: bool = True,
) -> str:
    if not rows:
        cf = (component_filter or "").strip()
        hint = f"(фильтр component={cf}) " if cf else ""
        return (
            hint + "runtime_errors.jsonl пуст, файл ещё не создавался или в хвосте нет совпадений."
        )
    ordered = list(reversed(rows)) if newest_first else list(rows)
    lines: List[str] = []
    for r in ordered:
        ts = format_operator_datetime_from_iso(r.get("ts")) or str(r.get("ts", ""))[:32]
        code = r.get("code", "")
        comp_v = r.get("component", "")
        sev = r.get("severity", "")
        msg = str(r.get("message", ""))[:200]
        sev_disp = RUNTIME_SEVERITY_RU.get(str(sev).lower(), str(sev))
        comp_disp = runtime_component_label_ru(str(comp_v)) if comp_v else str(comp_v)
        lines.append(f"{ts} [{sev_disp}] {code} · {comp_disp}: {msg}")
    return "\n".join(lines)


def _admin_acl_ids() -> set:
    """
    Права на /admin_*: объединение ADMIN_USER_IDS и ADMIN_NOTIFY_USER_IDS.
    Стартовое ЛС использует ADMIN_NOTIFY_USER_IDS, если он задан, иначе ADMIN_USER_IDS —
    без этого пользователь видел «Бот запущен», но команды молчали (не считался админом).
    """
    out: set = set()
    for key in ("ADMIN_USER_IDS", "ADMIN_NOTIFY_USER_IDS"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        out |= {x.strip() for x in raw.split(",") if x.strip()}
    return out


def admin_user_ids() -> list[int]:
    """Telegram user_id админов для рассылки (bug reports, уведомления)."""
    ids: list[int] = []
    for raw in _admin_acl_ids():
        s = str(raw).strip()
        if s.isdigit():
            ids.append(int(s))
    return ids


def report_commands_map() -> Dict[str, str]:
    """Тексты для блока «Команды отчёта» (HTML-фрагменты с <code>) и поля commands в /admin_system_json."""
    return {
        "full_check": "<code>/admin_system</code> — полная сводка в чате",
        "diagnostic_zip": "<code>/admin_diagnostic</code> — архив ZIP (запуск, окружение без секретов, производительность, снимки) · <code>/admin_diagnostic net</code> — то же + сеть",
        "bug_report": "<code>/admin_bug</code> — реплай на баг + тот же ZIP + <code>bug_report.json</code> + снимок логов; копия в <code>data/diagnostics/bug_reports/</code> · <code>/admin_bug net</code> · <code>/admin_bug 60 comp=voice заметка</code>",
        "connectivity": "<code>/admin_connectivity</code> — проверка Telegram, OpenRouter, Mem0 (~20 с)",
        "code_map": "<code>/admin_code_map</code> — карта .py и история; эталон: <code>/admin_code_baseline_set</code>, дифф: <code>/admin_code_baseline_diff_json</code>",
        "pulse": "<code>/admin_pulse</code> — «пульс»: счётчики, задержки, последние решения маршрутизатора",
        "xray": "<code>/admin_xray</code> — «рентген»: аномалии и узкие места",
        "memory_insight": "<code>/admin_memory_insight [N]</code> — хвост JSONL (strategy_paths, route_risk, experience) и сессия; время в тексте — короткий формат; <code>/admin_memory_insight_json</code>",
        "reasoning_quality": "<code>/admin_reasoning_quality</code> — качество reasoning: финальный ответ, завершённость, anti-meta; <code>/admin_reasoning_quality_json</code>",
        "usage_digest": "<code>/admin_usage_digest</code> — дайджест активности и тренды",
        "turns": "<code>/admin_turns [N] [issues]</code> — последние ходы из turns.jsonl (цепочка profile/lane/issues)",
        "access": "<code>/admin_access</code> — заявки в личку (кнопки)",
        "logs_tail": "<code>/admin_logs [N] [компонент]</code> — хвост журнала ошибок; пример: <code>/admin_logs 50 voice</code>",
        "health_dump": "<code>/admin_health</code> — единая сводка здоровья (HTML)",
        "resilience": "<code>/admin_resilience</code> — устойчивость и безопасный режим",
        "operator": "<code>/admin_operator</code> — консоль оператора (конфиг, голос, Mem0…)",
        "stats": "<code>/admin_stats</code> — счётчики мониторинга и ошибки",
        "llm_usage": "<code>/admin_llm_usage</code> — расход LLM: токены, стоимость, мини-график за 7 дней",
        "llm_usage_reset": "<code>/admin_llm_usage_reset confirm</code> — очистить журнал llm_usage.jsonl (runtime MONITOR не трогает)",
        "kv_debug": (
            "<code>/admin_kv_debug</code> — stickiness сессии LLM, reuse, rolling cache; "
            "<code>/admin_kv_debug_json</code> — то же + <code>prompt_breakdown</code>, <code>agent_pack</code>; "
            "<code>/admin_kv_branches</code>, <code>/admin_kv_rollback</code>, <code>/admin_kv_copy_branch</code>"
        ),
        "efficiency": "<code>/admin_efficiency</code> — эффективность: экономия токенов, успех плагинов, качество маршрутизации",
        "plugins_health": "<code>/admin_plugins_health</code> — здоровье плагинов: статус, slash-токены, конфликты",
        "housekeeping": "<code>/admin_housekeeping</code> — уборка мусора (dry-run; запуск: <code>/admin_housekeeping run</code>)",
        "self_model": "<code>/admin_self_model</code> — само-модель агента (кто/что/ограничения, из KV/сессии)",
        "session_task": (
            "<code>/admin_session_task</code> — последний маршрут и вызов инструмента мозга "
            "(сводка в <code>session_task</code> файла behavior); опционально: <code>&lt;user_id&gt; [group_id]</code>"
        ),
        "git_update": "<code>/admin_git &lt;commit message&gt;</code> — commit → push → pull (полный git pipeline)",
        "router_status": (
            "<code>/admin_router</code> — статус LLM-роутера: источник, задержки, профили; "
            "<code>/admin_router reset</code> — сброс кеша и метрик"
        ),
        "event_bus": (
            "<code>/admin_event_bus_history</code> — последние события шины; "
            "<code>/admin_event_bus_subscribers</code> — подписчики; "
            "<code>/admin_event_bus_healers</code> — состояние healers (включая AutoHealers); "
            "<code>/admin_bug_self_heal</code> — ретроспектива лечения; "
            "<code>/admin_bug_heal_triage</code> — запустить LLM-триаж; "
            "<code>/admin_bug_heal_list</code> — рекомендации; "
            "<code>/admin_bug_heal_apply</code> / <code>dismiss</code> — применить/отклонить"
        ),
        "undo_log": (
            "<code>/admin_undo_log</code> — undo-журнал авто-лечения; "
            "<code>/admin_undo_confirm &lt;id&gt;</code> — подтвердить; "
            "<code>/admin_undo_rollback &lt;id&gt;</code> — принудительный откат"
        ),
        "mce": (
            "<code>/admin_mce_status</code> — Meta-Cognitive Engine: self-state, drift, experiments, goals; "
            "<code>/admin_mce_ask &lt;вопрос&gt;</code> — задать вопрос MCE"
        ),
        "learning": (
            "<code>/admin_learning_digest</code> — уроки, experience, route_risk, топ скиллов; "
            "<code>/admin_route_risk_clusters</code> — кластеры ошибок; "
            "<code>/admin_reputation</code> — v_c по маршрутам и скиллам"
        ),
        "code_evolution": (
            "<code>/admin_patch_list</code> — патчи авто-оптимизации; "
            "<code>/admin_patch_status &lt;id&gt;</code> — детали; "
            "<code>/admin_patch_rollback &lt;id&gt;</code> — откат; "
            "<code>/admin_evol_log</code> — журнал эволюции кода"
        ),
    }


REPORT_COMMAND_KEYS: tuple[str, ...] = (
    "full_check",
    "diagnostic_zip",
    "bug_report",
    "connectivity",
    "code_map",
    "pulse",
    "xray",
    "memory_insight",
    "reasoning_quality",
    "usage_digest",
    "turns",
    "access",
    "logs_tail",
    "health_dump",
    "resilience",
    "operator",
    "stats",
    "llm_usage",
    "llm_usage_reset",
    "kv_debug",
    "efficiency",
    "plugins_health",
    "housekeeping",
    "self_model",
    "session_task",
    "git_update",
    "router_status",
    "event_bus",
    "undo_log",
    "mce",
    "learning",
    "code_evolution",
)


class AdminModule:
    def __init__(self, orchestrator: Any = None, behavior_store: Any = None) -> None:
        self.orchestrator = orchestrator
        self.behavior_store = behavior_store
        self.admin_ids = _admin_acl_ids()

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self.admin_ids

    def report_commands_bullets_html(self) -> str:
        m = report_commands_map()
        return "\n".join(f"• {m[k]}" for k in REPORT_COMMAND_KEYS)

    def report_commands_section_html(self, *, include_json_footer: bool = True) -> str:
        lines = [
            "<b>Команды отчёта</b>",
            "<i>В веб-клиенте Telegram строки с <code>/…</code> часто только копируются; "
            "те же команды — <b>кнопками под сообщением</b> (как в /help → Админ).</i>",
            "",
            self.report_commands_bullets_html(),
        ]
        if include_json_footer:
            lines.extend(["", "<i>Полный JSON: <code>/admin_system_json</code></i>"])
        return "\n".join(lines)

    def menu_intro_html(self) -> str:
        """Краткий вход в /admin — без простыни команд (полный список: «Команды» или /help)."""
        h = self.health_summary()
        safe = False
        rc = getattr(self.orchestrator, "_resilience", None)
        if rc is not None and rc.is_enabled():
            try:
                safe = bool(rc.is_safe_mode())
            except Exception as e:
                logger.debug("%s optional failed: %s", "admin_module", e, exc_info=True)
        return (
            "🔧 <b>Панель администратора</b>\n\n"
            "<blockquote>"
            f"Состояние: <b>{esc(ru_status(h.get('overall_status')))}</b> · "
            f"safe mode: <b>{'да ⚠️' if safe else 'нет'}</b>\n\n"
            "Кнопки ниже — превью разделов. Полные отчёты — внутри раздела или "
            "<code>/admin_system</code>.\n"
            "Справочник команд: «📖 Команды» или <code>/help</code> → Админ."
            "</blockquote>"
        )

    def menu_hub_html(self, *, page: int = 1) -> str:
        """Экран выбора раздела (листание меню)."""
        from core.input_handlers.telegram_nav import admin_menu_page_count

        total = admin_menu_page_count()
        page = max(1, min(page, total))
        return (
            f"📋 <b>Меню админа</b> <i>({page}/{total})</i>\n\n"
            "<blockquote>"
            "Выберите раздел. ◀️ ▶️ — листать страницы. "
            "«◀️ Панель» на экранах разделов возвращает к обзору."
            "</blockquote>"
        )

    def menu_text(self) -> str:
        return (
            "Админ-панель (HTML): <code>/admin</code> · JSON: команды с суффиксом <code>_json</code> "
            "(например <code>/admin_health_json</code>). См. /help → Админ."
        )

    def menu_keyboard(self, page: int = 1) -> InlineKeyboardMarkup:
        from core.input_handlers.telegram_nav import build_admin_menu_keyboard

        return build_admin_menu_keyboard(page)

    def commands_quick_keyboard(self) -> InlineKeyboardMarkup:
        """Переходы между разделами списка команд (help:admin_*), плюс возврат в меню."""
        from core.input_handlers.help_payload import admin_commands_panel_rows

        return InlineKeyboardMarkup(inline_keyboard=admin_commands_panel_rows(include_dashboard_back=True))

    def settings_panel_html(self) -> str:
        from core.runtime_telegram_settings import _store_path

        lines = [
            "⚙️ <b>Настройки (Telegram)</b>",
            "",
            "<blockquote>",
            "Нажмите кнопку — значение переключится и сохранится на сервере "
            "(файл JSON, приоритет над переменными окружения для этих ключей).",
            f"<i>Путь: <code>{esc(str(_store_path()))}</code></i>",
            "</blockquote>",
            "",
        ]
        toggles: List[str] = []
        for _tid, env_key, title, dflt in TOGGLE_DEFS:
            on = effective_bool(env_key, default=dflt)
            toggles.append(
                f"• <b>{esc(title)}</b>\n  <code>{esc(env_key)}</code> → "
                f"<b>{'вкл' if on else 'выкл'}</b>"
            )
        lines.extend(["🔧 <b>Параметры</b>", "", "<blockquote>", *toggles, "</blockquote>", ""])
        lines.append(
            "<blockquote><i>Снимок в JSON: <code>/admin_operator_json</code> → telegram_runtime_settings</i></blockquote>"
        )
        return "\n".join(lines)

    def settings_keyboard(self) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for tid, env_key, title, dflt in TOGGLE_DEFS:
            on = effective_bool(env_key, default=dflt)
            mark = "✓" if on else "·"
            rows.append(
                [InlineKeyboardButton(text=f"{mark} {title}", callback_data=f"aus:{tid}")]
            )
        rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="admin:dashboard")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def dashboard_html(self) -> str:
        h = self.health_summary()
        rc = getattr(self.orchestrator, "_resilience", None)
        safe = False
        if rc is not None and rc.is_enabled():
            try:
                safe = bool(rc.is_safe_mode())
            except Exception as e:
                logger.debug('%s optional failed: %s', 'admin_module', e, exc_info=True)
        snap = build_diagnostic_snapshot(self.orchestrator)
        err = snap.get("errors") or {}
        sysb = snap.get("system") or {}
        nmod = len(sysb.get("modules") or []) if isinstance(sysb, dict) else 0
        parts = [
            "📊 <b>Сводка</b>",
            "",
            "<blockquote>",
            "<i>Быстрый обзор. Подробности — кнопки «Пульс», «Рентген», «LLM».</i>",
            "",
            f"Состояние: <b>{esc(ru_status(h.get('overall_status')))}</b>",
            f"Модулей в отчёте: <b>{nmod}</b>",
            f"Безопасный режим: <b>{'да ⚠️' if safe else 'нет'}</b>",
            "</blockquote>",
            "",
            format_errors_compact_html(err if isinstance(err, dict) else {}),
        ]
        return "\n".join(parts)

    def reputation_teaser_html(self) -> str:
        lines = [
            "⭐ <b>Репутация маршрутов и скиллов</b>",
            "",
            "<blockquote>",
            "<i>v_c — кооперативная уверенность, v_p — штрафной поток. "
            "Скиллы накапливаются при ходах с skill_name (CDC).</i>",
            "</blockquote>",
            "",
            "<blockquote><i>Отчёт: <code>/admin_reputation</code> "
            "(опц. USER_ID) · JSON: <code>/admin_reputation_json</code></i></blockquote>",
        ]
        return "\n".join(lines)

    def learning_teaser_html(self) -> str:
        try:
            from core.learning_digest import format_learning_digest_html, build_learning_digest

            d = build_learning_digest()
            return format_learning_digest_html(d)
        except Exception as e:
            return "\n".join(
                [
                    "🧠 <b>Дайджест обучения</b>",
                    "",
                    f"<blockquote><i>{esc(str(e)[:200])}</i></blockquote>",
                    "",
                    "<blockquote><i><code>/admin_learning_digest</code></i></blockquote>",
                ]
            )

    def stats_summary_html(self) -> str:
        snap = build_diagnostic_snapshot(self.orchestrator)
        mon = snap.get("monitoring") or {}
        cnt = mon.get("counters") or {}
        lines = [
            "📈 <b>Мониторинг</b>",
            "",
            "<blockquote><i>Счётчики событий с момента запуска бота.</i></blockquote>",
            "",
        ]
        if isinstance(cnt, dict) and cnt:
            lines.append(format_top_counters_chart_html(cnt, limit=16))
            lines.append("")
            if len(cnt) > 16:
                lines.append(
                    "<blockquote>"
                    f"<i>Всего показателей: <b>{len(cnt)}</b> — полный список: <code>/admin_stats_json</code></i>"
                    "</blockquote>"
                )
        else:
            lines.append("<blockquote><i>Нет данных счётчиков.</i></blockquote>")
        lines.append("")
        lines.append(format_errors_compact_html(snap.get("errors") or {}))
        lines.append("")
        lines.append("<blockquote><i>Полный JSON: <code>/admin_stats_json</code></i></blockquote>")
        return "\n".join(lines)

    def llm_usage_teaser_html(self) -> str:
        """Короткая сводка для кнопки «LLM» (полный отчёт — /admin_llm_usage)."""
        from core.llm_usage_store import aggregate_usage, unicode_sparkline
        from core.monitoring import MONITOR

        agg = aggregate_usage(days=7.0)
        lines = [
            "📉 <b>LLM · OpenRouter</b> <i>(окно 7 дн.)</i>",
            "",
            "<blockquote><i>Краткая сводка; полный отчёт — команда ниже.</i></blockquote>",
            "",
        ]
        teaser_rows: List[Tuple[str, str]] = [
            ("Записей", str(int(agg.get("window_records") or 0))),
            ("Успехи", str(int(agg.get("completions_ok") or 0))),
            ("Сбои", str(int(agg.get("completions_fail") or 0))),
            ("Токены", str(int(agg.get("total_tokens") or 0))),
            ("Cost $", str(round(float(agg.get("cost_sum") or 0), 6))),
        ]
        nanos = int(MONITOR.counters.get("openrouter_cost_credits_nanos_total", 0))
        if nanos:
            teaser_rows.append(("MONITOR $", str(round(nanos / 1e9, 6))))
        lines.extend(["📋 <b>Сводка</b>", "", "<blockquote>", report_pre_kv(teaser_rows), "</blockquote>", ""])
        st = agg.get("sparkline_tokens") or []
        days_lbl = agg.get("sparkline_days") if isinstance(agg.get("sparkline_days"), list) else []
        if st:
            sl = unicode_sparkline([float(x) for x in st])
            sp_lbl: List[Tuple[str, str]] = [("7 дн.", sl)]
            if days_lbl:
                sp_lbl.insert(0, ("Даты", f"{days_lbl[0]} … {days_lbl[-1]}"))
            lines.extend(
                [
                    "📈 <b>Токены по дням</b>",
                    "",
                    "<blockquote>",
                    report_pre_kv(sp_lbl, value_max=44),
                    "</blockquote>",
                    "",
                ]
            )
        lines.append("<blockquote><i>Полный отчёт: <code>/admin_llm_usage</code></i></blockquote>")
        return "\n".join(lines)

    def kv_debug_snapshot(self, *, user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        from core.brain.session_stickiness import debug_snapshot as kv_debug_session
        from core.llm_usage_store import recent_rows
        from core.monitoring import MONITOR

        try:
            w = int((os.getenv("ADMIN_KV_DEBUG_WINDOW_ROWS") or "20").strip() or "20")
        except ValueError:
            w = 20
        w = max(5, min(w, 200))
        sid = kv_debug_session(user_id=user_id, group_id=group_id)
        rows = recent_rows(days=30.0)
        target_sid = str(sid.get("session_id") or "")
        matched = [
            r for r in rows
            if isinstance(r, dict) and str(r.get("session_id") or "") == target_sid
        ]
        matched.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        last = matched[0] if matched else {}
        ok_rows = [r for r in matched if isinstance(r, dict) and r.get("ok")]
        window_rows = ok_rows[:w]
        cached_tok = int(last.get("cached_prompt_tokens") or 0) if last else 0
        prompt_tok = int(last.get("prompt_tokens") or 0) if last else 0
        pb = last.get("prompt_breakdown") if isinstance(last, dict) and isinstance(last.get("prompt_breakdown"), dict) else {}
        session_hits = 0
        session_misses = 0
        for r in ok_rows:
            cpt = int(r.get("cached_prompt_tokens") or 0)
            if cpt > 0:
                session_hits += 1
            else:
                session_misses += 1
        session_total = session_hits + session_misses
        session_hit_rate = (float(session_hits) / float(session_total)) if session_total else 0.0
        win_cached = 0
        win_prompt = 0
        win_hits = 0
        for r in window_rows:
            p = int(r.get("prompt_tokens") or 0)
            c = int(r.get("cached_prompt_tokens") or 0)
            win_prompt += max(0, p)
            win_cached += max(0, c)
            if c > 0:
                win_hits += 1
        win_total = len(window_rows)
        win_hit_rate = (float(win_hits) / float(win_total)) if win_total else 0.0
        win_coverage = (float(win_cached) / float(win_prompt)) if win_prompt else 0.0
        counters = MONITOR.snapshot().get("counters") if isinstance(MONITOR.snapshot(), dict) else {}
        return {
            "session": sid,
            "latest": {
                "ts": last.get("ts"),
                "cached_tok": cached_tok,
                "prompt_tokens": prompt_tok,
                "prompt_breakdown": pb,
            },
            "reuse_hits": int((counters or {}).get("openrouter_prompt_reuse_hits_total", 0)),
            "reuse_misses": int((counters or {}).get("openrouter_prompt_reuse_misses_total", 0)),
            "rows_for_session": len(matched),
            "session_window": {
                "hits": session_hits,
                "misses": session_misses,
                "total": session_total,
                "hit_rate": session_hit_rate,
            },
            "rolling_window": {
                "rows": win_total,
                "rows_limit": w,
                "cached_tokens_sum": win_cached,
                "prompt_tokens_sum": win_prompt,
                "cache_coverage": win_coverage,
                "hits": win_hits,
                "hit_rate": win_hit_rate,
            },
        }

    def router_status_snapshot(self) -> Dict[str, Any]:
        from core.brain.router_classifier import router_metrics, lru_size, permanent_size, raw_log_size
        m = router_metrics()
        return {
            "lru_size": lru_size(),
            "permanent_size": permanent_size(),
            "raw_log_size": raw_log_size(),
            "metrics": m,
        }

    def router_reset(self) -> int:
        from core.brain.router_classifier import (_lru_clear, reset_metrics,
                                                   reset_raw_log, trigger_frequency_sweep)
        n = _lru_clear()
        reset_metrics()
        trigger_frequency_sweep()
        return n

    def resilience_panel_html(self) -> str:
        rc = getattr(self.orchestrator, "_resilience", None)
        if rc is None:
            return "\n".join(
                ["🛡️ <b>Устойчивость</b>", "", "<blockquote><i>контроллер недоступен</i></blockquote>"]
            )
        try:
            ev = rc.evaluate(self.orchestrator)
            sn = rc.snapshot()
        except Exception as e:
            return "\n".join(
                [
                    "🛡️ <b>Устойчивость</b>",
                    "",
                    "<blockquote>",
                    f"<i>ошибка: {esc(e)}</i>",
                    "</blockquote>",
                ]
            )
        lines = [
            "🛡️ <b>Устойчивость</b>",
            "",
            "<blockquote><i>Резервный режим и пороги по журналу ошибок.</i></blockquote>",
            "",
        ]
        eth = ev.get("error_thresholds") if isinstance(ev.get("error_thresholds"), dict) else {}
        mod_raw = ev.get("modules_overall") or ev.get("overall_status")
        res_tbl: List[Tuple[str, str]] = [
            ("KPI ок", ru_bool(ev.get("kpi_ok"))),
            ("Ошибок в журнале", str(ev.get("error_total", ""))),
        ]
        if eth.get("degraded_at") is not None and eth.get("critical_at") is not None:
            res_tbl.append(("Порог тревоги", str(eth.get("degraded_at"))))
            res_tbl.append(("Порог критич.", str(eth.get("critical_at"))))
        res_tbl.extend(
            [
                ("Деградация", ru_bool(ev.get("degraded"))),
                ("Критично", ru_bool(ev.get("critical"))),
                ("Модули", f"{ru_status(mod_raw)} ({mod_raw})"),
            ]
        )
        lines.extend(
            ["📊 <b>Оценка</b>", "", "<blockquote>", report_pre_kv(res_tbl, value_max=36), "</blockquote>", ""]
        )
        sm = (sn.get("safe_mode") or {}) if isinstance(sn, dict) else {}
        sm_lines: List[str]
        if isinstance(sm, dict):
            if not sm:
                sm_lines = ["Безопасный режим: <i>не активен / нет данных</i>"]
            else:
                sm_lines = [report_pre_kv([("Безоп. режим", ru_bool(sm.get("active")))])]
                if sm.get("reason"):
                    sm_lines.append(f"<i>Причина: {esc(sm.get('reason'))}</i>")
        else:
            sm_lines = ["<i>нет данных</i>"]
        lines.extend(["🔒 <b>Safe mode</b>", "", "<blockquote>", *sm_lines, "</blockquote>", ""])
        lines.append("<blockquote><i>Подробно: <code>/admin_resilience</code></i></blockquote>")
        return "\n".join(lines)

    def backups_teaser_html(self) -> str:
        try:
            ra = self.orchestrator._recovery_autonomy
            rows = ra.list_backups()
        except Exception as e:
            return "\n".join(
                ["💾 <b>Бэкапы</b>", "", "<blockquote>", f"<i>{esc(e)}</i>", "</blockquote>"]
            )
        if not isinstance(rows, list):
            return "\n".join(
                ["💾 <b>Бэкапы</b>", "", "<blockquote><i>нет данных</i></blockquote>"]
            )
        inner: List[str] = [f"Записей: <b>{len(rows)}</b>", ""]
        for r in rows[:10]:
            if isinstance(r, dict):
                rid = r.get("id") or r.get("path") or r
                inner.append(f"• <code>{esc(rid)}</code>")
            else:
                inner.append(f"• <code>{esc(r)}</code>")
        inner.extend(
            [
                "",
                "<i><code>/admin_backup_list</code> · <code>/admin_backup_run</code> · <code>/admin_restore latest</code></i>",
            ]
        )
        return "\n".join(["💾 <b>Бэкапы</b>", "", "<blockquote>", *inner, "</blockquote>"])

    def passport_teaser_html(self) -> str:
        try:
            p = get_development_passport()
        except Exception as e:
            return "\n".join(
                ["📜 <b>Паспорт разработки</b>", "", "<blockquote>", f"<i>{esc(e)}</i>", "</blockquote>"]
            )
        m = str(p.get("mission") or "").strip()
        if len(m) > 600:
            m = m[:597] + "…"
        src = get_passport_source_info()
        mission_html = esc(m) if m else "<i>mission пуст</i>"
        inner = [
            mission_html,
            "",
            f"<i>источник: {esc(str(src))}</i>",
            "",
            "<i><code>/admin_passport</code> — полностью</i>",
        ]
        return "\n".join(["📜 <b>Паспорт разработки</b>", "", "<blockquote>", *inner, "</blockquote>"])

    def commands_cheatsheet_html(self) -> str:
        return (
            "📖 <b>Команды администратора</b>\n\n"
            "<blockquote>"
            "Разделы ниже — справочник (команды с кнопок не отправляются). "
            "Быстрые отчёты: «⭐ Статистика» в справке или разделы панели /admin. "
            "Полная сводка: <code>/admin_system</code> · JSON: <code>/admin_system_json</code>."
            "</blockquote>"
        )

    def operator_teaser_html(self) -> str:
        snap = self.operator_console_snapshot()
        h = snap.get("health") or {}
        cv = snap.get("config_validation") or {}
        voice = snap.get("voice_stt") or {}
        lines = ["🎛 <b>Консоль оператора</b>", ""]
        lines.append(format_health_short_html(h if isinstance(h, dict) else {}))
        lines.append("")
        if isinstance(cv, dict) and cv:
            cv_in = [f"Конфиг ok: <b>{esc(cv.get('ok'))}</b>"]
            if cv.get("errors"):
                cv_in.append(f"ошибки: <code>{esc(cv.get('errors'))}</code>")
            if cv.get("warnings"):
                cv_in.append(f"предупреждения: <code>{esc(cv.get('warnings'))}</code>")
            lines.extend(["⚙️ <b>Конфиг</b>", "", "<blockquote>", *cv_in, "</blockquote>", ""])
        if isinstance(voice, dict) and voice:
            vlines = [f"• {esc(k)}: {esc(v)}" for k, v in list(voice.items())[:8]]
            lines.extend(["🎙 <b>STT</b>", "", "<blockquote>", *vlines, "</blockquote>", ""])
        lines.append("<blockquote><i>Полный JSON: <code>/admin_operator_json</code></i></blockquote>")
        return "\n".join(lines)

    def logs_teaser_html(self) -> str:
        tail = self.tail_runtime_errors_text(12)
        return "\n".join(
            [
                "📜 <b>Журнал ошибок</b> <i>(последние 12)</i>",
                "",
                "<blockquote>",
                code_block_html(tail),
                "</blockquote>",
                "",
                "<blockquote><i>Больше строк: <code>/admin_logs 40</code></i></blockquote>",
            ]
        )

    def callback_body_html(self, key: str) -> str:
        key = (key or "").lower()
        if key == "dashboard":
            return self.dashboard_html()
        if key == "stats":
            return self.stats_summary_html()
        if key == "pulse":
            return format_pulse_html(self.live_pulse_snapshot())
        if key == "xray":
            return format_xray_html(self.xray_snapshot())
        if key == "llm_usage":
            return self.llm_usage_teaser_html()
        if key == "antiflood":
            return format_anti_flood_html(self.anti_flood_summary())
        if key == "skills":
            return "\n".join(
                [
                    "🎯 <b>Навыки мозга</b>",
                    "",
                    "<blockquote>",
                    "<code>/admin_toggle_skill &lt;имя&gt;</code> — вкл/выкл skill в brain",
                    "<code>/admin_reputation</code> — уверенность v_c по каждому skill",
                    "<code>/admin_learning_digest</code> — уроки, опыт, кластеры ошибок",
                    "</blockquote>",
                ]
            )
        if key == "reputation":
            return self.reputation_teaser_html()
        if key == "learning":
            return self.learning_teaser_html()
        if key == "logs":
            return self.logs_teaser_html()
        if key == "users":
            return "\n".join(
                [
                    "👥 <b>Пользователи</b>",
                    "",
                    "<blockquote>",
                    "<code>/admin_access</code> — модерация входа в личку (кнопки).",
                    "<code>/admin_facts &lt;telegram_user_id&gt;</code> — факты пользователя.",
                    "Сам пользователь: <code>/me</code>, <code>/facts</code>.",
                    "</blockquote>",
                ]
            )
        if key == "facts":
            return "\n".join(
                [
                    "📝 <b>Факты</b>",
                    "",
                    "<blockquote>",
                    "<code>/facts</code> · <code>/forget поле</code> · <code>/facts_refresh</code> · <code>/facts_reset</code>",
                    "</blockquote>",
                ]
            )
        if key == "security":
            return "\n".join(
                [
                    "🔒 <b>Безопасность</b>",
                    "",
                    "<blockquote>",
                    "Антифлуд, проверка ссылок, лимиты файлов. Кнопка «Flood» — параметры.",
                    "<code>/admin_governance</code> — хранение логов.",
                    "</blockquote>",
                ]
            )
        if key == "auto":
            return "\n".join(
                [
                    "🤖 <b>Автономия</b>",
                    "",
                    "<blockquote>",
                    "<code>/auto_suggestions</code>",
                    "<code>/auto_review</code>",
                    "<code>/auto_idea тема</code>",
                    "</blockquote>",
                ]
            )
        if key == "health":
            return "\n".join(
                [
                    format_health_short_html(self.health_summary()),
                    "",
                    "<blockquote><i><code>/admin_health</code> — подробно в HTML; JSON: <code>/admin_health_json</code></i></blockquote>",
                ]
            )
        if key == "resilience":
            return self.resilience_panel_html()
        if key == "governance":
            return format_governance_html(self.governance_status())
        if key == "backups":
            return self.backups_teaser_html()
        if key == "passport":
            return self.passport_teaser_html()
        if key == "operator":
            return self.operator_teaser_html()
        if key == "settings":
            return self.settings_panel_html()
        if key == "commands":
            return self.commands_cheatsheet_html()
        if key == "seed_menu":
            return "\n".join(
                [
                    "📎 <b>Сиды конфигурации</b>",
                    "",
                    "<blockquote>",
                    "Копия из <code>config/system_directive_addon_v3.example.txt</code> и "
                    "<code>config/operator_rules.example.json</code> в каталог рантайма "
                    "(см. <code>SYSTEM_DIRECTIVE_ADDON_PATH</code>, <code>OPERATOR_RULES_PATH</code>).",
                    "",
                    "<b>Заполнить пустые</b> — только если файла нет или он пустой.",
                    "<b>Директива (force)</b> — перезаписать <code>system_directive_addon.txt</code> из примера.",
                    "<b>Всё из примеров</b> — также перезаписать <code>operator_rules.json</code> "
                    "(осторожно: затрёт ваши правки в JSON).",
                    "",
                    "<i>Команда: <code>/admin_seed_runtime</code> — без аргументов (только пустые); "
                    "<code>force</code> — директива; <code>all</code> — директива + JSON.</i>",
                    "</blockquote>",
                ]
            )
        return "<i>Неизвестное действие.</i>"

    def callback_view(self, key: str) -> str:
        """Совместимость: отдаёт текст без HTML (устарело)."""
        return self.callback_body_html(key)

    def stats(self) -> Dict[str, Any]:
        snap = build_diagnostic_snapshot(self.orchestrator)
        return {
            "system": snap.get("system", {}),
            "monitoring": snap.get("monitoring", {}),
            "errors": snap.get("errors", {}),
            "diagnostics": snap,
        }

    def governance_status(self) -> Dict[str, Any]:
        return {
            "retention_days_logs": DG.retention_days_logs,
            "retention_days_behavior": DG.retention_days_behavior,
            "redact_keys": sorted(DG.redact_keys),
        }

    def user_facts_summary(self, user_id: str) -> Dict[str, Any]:
        if not self.behavior_store:
            return {"error": "behavior store unavailable"}
        rec = self.behavior_store.load(str(user_id), None)
        return {"user_id": str(user_id), "facts": rec.get("user_facts", {}), "facts_meta": rec.get("user_facts_meta", {})}

    def anti_flood_summary(self) -> Dict[str, Any]:
        if not self.orchestrator:
            return {}
        return {
            "ANTI_FLOOD_ENABLED": self.orchestrator.anti_flood_enabled,
            "MAX_MSG_PER_10S": self.orchestrator.max_msg_per_10s,
            "MAX_SAME_TEXT": self.orchestrator.max_same_text,
            "MAX_CMD_PER_10S": self.orchestrator.max_cmd_per_10s,
            "GROUP_COOLDOWN_SEC": self.orchestrator.group_cooldown_sec,
        }

    def health_summary(self) -> Dict[str, Any]:
        snap = build_diagnostic_snapshot(self.orchestrator)
        system = snap.get("system", {})
        security = snap.get("security", {})
        mon = snap.get("monitoring", {}) if isinstance(snap.get("monitoring"), dict) else {}
        counters = mon.get("counters", {}) if isinstance(mon.get("counters"), dict) else {}
        try:
            from core.reasoning_status import load_reasoning_quality_snapshot

            reasoning_quality = load_reasoning_quality_snapshot()
        except Exception:
            reasoning_quality = {}
        return {
            "overall_status": system.get("overall_status", "unknown"),
            "mode": system.get("mode", "unknown"),
            "active_traces": (snap.get("observability", {}) or {}).get("active_traces", 0),
            "security": security,
            "input_pipeline": {
                "skipped_no_actor_total": int(counters.get("input_skipped_no_actor_total", 0)),
            },
            "planner_engine": (system.get("planner", {}) or {}).get("engine", ""),
            "reasoning_quality": reasoning_quality,
        }

    def reasoning_quality_snapshot(self) -> Dict[str, Any]:
        try:
            from core.reasoning_status import load_reasoning_quality_snapshot

            snap = load_reasoning_quality_snapshot()
        except Exception as e:
            return {"ok": False, "error": f"reasoning quality unavailable: {e}"}
        if not isinstance(snap, dict) or not snap:
            return {"ok": False, "error": "reasoning quality snapshot is empty"}
        out = dict(snap)
        out["ok"] = bool(
            out.get("final_answer_present")
            and out.get("reasoning_completed")
            and out.get("no_meta_text")
        )
        return out

    def tail_runtime_errors_text(self, n: int = 25, *, component: str | None = None, newest_first: bool = True) -> str:
        n = max(1, min(int(n), 100))
        comp = (component or "").strip() or None
        rows = read_recent_events(limit=n, component=comp)
        return _format_runtime_error_lines(rows, component_filter=comp, newest_first=newest_first)

    def runtime_errors_file_meta(self) -> Dict[str, Any]:
        return runtime_errors_file_meta()

    def admin_logs_snapshot(self, n: int, *, component: str | None = None) -> Dict[str, Any]:
        """Один проход чтения журнала для /admin_logs (тело + метаданные файла)."""
        n = max(1, min(int(n), 100))
        comp = (component or "").strip() or None
        meta = runtime_errors_file_meta()
        rows = read_recent_events(limit=n, component=comp)
        body = _format_runtime_error_lines(rows, component_filter=comp, newest_first=True)
        newest_ts = str(rows[-1].get("ts", ""))[:32] if rows else ""
        return {
            "body": body,
            "file_meta": meta,
            "newest_ts": newest_ts,
            "component_filter": comp or "",
            "n": n,
        }

    def live_pulse_snapshot(self) -> Dict[str, Any]:
        from core.live_pulse import build_pulse_snapshot

        return build_pulse_snapshot(self.orchestrator)

    def xray_snapshot(self) -> Dict[str, Any]:
        from core.live_pulse import build_xray_snapshot

        return build_xray_snapshot(self.orchestrator)

    def full_system_report(self) -> Dict[str, Any]:
        """Сводка для оператора: единый health, оценка resilience, подсказки по командам."""
        uh = build_unified_health_snapshot(self.orchestrator)
        ev = uh.get("evaluate") if isinstance(uh.get("evaluate"), dict) else {}
        return {
            "unified_health": uh,
            "resilience_evaluate": ev,
            "commands": dict(report_commands_map()),
        }

    def plugin_health_snapshot(self) -> Dict[str, Any]:
        reg = getattr(self, "orchestrator", None)
        plugin_registry = getattr(reg, "plugin_registry", None) if reg is not None else None
        if plugin_registry is None:
            return {"ok": False, "error": "plugin_registry unavailable"}

        try:
            from core.plugin_contract import validate_registry as _validate_registry

            contract_audit = _validate_registry(plugin_registry)
        except Exception as _e:
            contract_audit = {"error": f"contract validation failed: {_e}"}

        all_mods = getattr(plugin_registry, "modules", {}) or {}
        loaded = getattr(plugin_registry, "loaded_modules", {}) or {}
        token_owners: Dict[str, List[str]] = {}
        rows: List[Dict[str, Any]] = []

        for name, mod in sorted(all_mods.items(), key=lambda t: str(t[0])):
            manifest = getattr(mod, "manifest", None)
            state = getattr(mod, "state", None)
            tokens = manifest.iter_command_tokens() if manifest and hasattr(manifest, "iter_command_tokens") else []
            uniq_tokens = sorted({str(t).strip().lstrip("/").lower() for t in tokens if str(t).strip()})
            for t in uniq_tokens:
                token_owners.setdefault(t, []).append(str(name))
            rows.append(
                {
                    "name": str(name),
                    "loaded": str(name) in loaded,
                    "status": str(getattr(state, "status", "") or ""),
                    "commands_count": len(uniq_tokens),
                    "commands": uniq_tokens,
                    "last_error": str(getattr(state, "last_error", "") or ""),
                }
            )

        collisions = {
            tok: owners
            for tok, owners in sorted(token_owners.items(), key=lambda t: t[0])
            if len(owners) > 1
        }
        loaded_without_commands = [
            r["name"]
            for r in rows
            if bool(r.get("loaded")) and int(r.get("commands_count") or 0) == 0
        ]
        failed_loaded = [
            r["name"]
            for r in rows
            if bool(r.get("loaded")) and str(r.get("status") or "").lower() in {"failed", "degraded"}
        ]
        return {
            "ok": True,
            "summary": {
                "registered_total": len(rows),
                "loaded_total": len(loaded),
                "failed_loaded_total": len(failed_loaded),
                "collision_tokens_total": len(collisions),
                "loaded_without_commands_total": len(loaded_without_commands),
                "contract_errors_total": int(
                    contract_audit.get("with_errors", 0) if isinstance(contract_audit, dict) else 0
                ),
                "contract_warnings_total": int(
                    contract_audit.get("with_warnings", 0) if isinstance(contract_audit, dict) else 0
                ),
            },
            "failed_loaded": failed_loaded,
            "loaded_without_commands": loaded_without_commands,
            "command_collisions": collisions,
            "plugins": rows,
            "contract_audit": contract_audit,
        }

    def development_passport_view(self) -> Dict[str, Any]:
        snap = build_diagnostic_snapshot(self.orchestrator)
        block = dict(snap.get("development_passport") or {})
        block["source"] = get_passport_source_info()
        return block

    def operator_console_snapshot(self) -> Dict[str, Any]:
        cfg = get_config()
        vm = VoiceModule()
        return {
            "health": self.health_summary(),
            "voice_stt": vm.stt_status(),
            "telegram_runtime_settings": snapshot_for_operator(),
            "operator_rules": operator_rules_snapshot(),
            "ephemeral_lessons": ephemeral_lessons_snapshot(),
            "ephemeral_autolearn": ephemeral_autolearn_snapshot(),
            "mem0": mem0_operator_diagnostics(),
            "config_validation": cfg.validate(),
            "operator_notes": {
                "admin_acl": "Доступ к админ-командам только у Telegram ID из ADMIN_USER_IDS; не публикуйте список.",
                "long_json": "Большие ответы режутся на несколько сообщений (см. telegram_util).",
                "stt_doc": "Подключение STT: docs/OPERATIONS_AND_ADMIN.md раздел «Голос (STT)».",
                "telegram_settings": "/admin → «Настройки»: погода wttr.in, STT локально, OpenRouter fallback, reasoning — без ПК.",
                "operator_rules": "Правила без кода: config/operator_rules.example.json → скопировать в data/runtime/operator_rules.json (или OPERATOR_RULES_PATH). См. operator_rules в /admin_operator_json.",
                "ephemeral_lessons": "Временные латки: авто из повторных правок (доверенные ID), /remember_patch, /clear_all_patches (full|queue), /pending_suggested_patch, /approve_suggested_patch, /export_patches, /forget_patch.",
                "mem0_doc": "Mem0: сверяйте key_sha256_12 в JSON после смены ключа; 401 mirror → MEM0_MIRROR_WRITE=false или верный MEM0_MIRROR_API_KEY.",
            },
        }
