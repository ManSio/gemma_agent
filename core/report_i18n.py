"""
Русские подписи для админ- и пользовательских отчётов в Telegram.
Технические ключи в JSON (/admin_*_json) не меняются — меняется только отображение в HTML.
"""
from __future__ import annotations

import logging

import html
from typing import Any, Dict, List, Tuple

# Статусы оркестратора / health
STATUS_VALUE_RU: dict[str, str] = {
    "healthy": "всё в норме",
    "degraded": "есть проблемы, частично работает",
    "failed": "критично, модули недоступны",
    "unknown": "неизвестно",
    "ok": "ок",
    "true": "да",
    "false": "нет",
}

# Счётчики MONITOR (ядро + мозг + OpenRouter + прочее) — короткие подписи для Telegram
MONITOR_KEY_RU: dict[str, str] = {
    "input_messages_total": "Входящие",
    "execute_plan_calls": "Запуск плана",
    "plan_calls": "Планировщик",
    "planner_decisions_total": "Маршрутизация",
    "planner_fallback_total": "Fallback",
    "telegram_reply_suspect_incomplete_total": "Обрыв ответа (эвр.)",
    "trace_finished_total": "Трассировки",
    "openrouter_completion_ok_total": "LLM успех",
    "openrouter_completion_fail_total": "Ошибки LLM",
    "openrouter_prompt_tokens_total": "Токены prompt",
    "openrouter_completion_tokens_total": "Токены ответа",
    "openrouter_prompt_cache_read_tokens_total": "Токены из кэша",
    "openrouter_paid_completions_total": "Платные LLM",
    "openrouter_cost_credits_nanos_total": "Стоимость (наносессия)",
    "maintenance_cycles_total": "Самообслуживание",
    "flood_blocked_total": "Стоп антифлуда",
    "security_high_risk_total": "Риск: высокий",
    "security_warning_total": "Предупреждения",
    "link_safety_flagged_total": "Ссылки: проверка",
    "link_safety_dangerous_total": "Ссылки: опасные",
    "brain_response_cache_hit_total": "Кэш ответов",
    "brain_hot_path_slim_total": "Hot path мозга",
    "brain_chat_context_slim_total": "Chat context slim (мозг)",
    "brain_chat_context_slim_drop_tools_index_total": "Chat slim: сброшен полный индекс инструментов",
    "brain_agent_pack_full_total": "Agent pack: полный монолит",
    "brain_agent_pack_chat_total": "Agent pack: chat-core",
    "brain_fast_chitchat_total": "Без тяжёлого LLM",
    "brain_tool_call_invalid_total": "Инструменты: ошибка",
    "brain_tool_call_retry_total": "Инструменты: повтор",
    "brain_tool_dedup_hit_total": "Инструменты: дедуп",
    "brain_tool_chain_ok_total": "Цепочки инструментов",
    "brain_tools_filtered_total": "Отфильтр. инструментов",
    "strategy_llm_outline_ok_total": "Контур плана (LLM)",
    "knowledge_rows_ingested_total": "Строк знаний",
    "knowledge_hint_policy_fresh_total": "Политика знаний",
    "predictive_confident_total": "Предсказания поведения",
    "lookahead_plan_built_total": "Планы «вперёд»",
    "plugin_hot_install_ok": "Hot install плагина",
    "resilience_safe_mode_enter_total": "Вход в safe mode",
    "resilience_safe_mode_exit_total": "Выход из safe mode",
    "resilience_restart_flag_total": "Флаг перезапуска",
    "resilience_restart_flag_cleared_total": "Сброс флага",
    "resilience_critical_actions_total": "Критич. резильенс",
    "host_resource_pressure_changes_total": "Смена нагрузки хоста",
    "host_resource_pressure_critical_total": "Ресурсы: критич.",
    "autonomy_backup_bundles_total": "Бэкапы созданы",
    "autonomy_restore_bundles_total": "Восстановления",
    "autonomy_auto_restore_passport_total": "Авто: паспорт",
    "autonomy_auto_restore_runtime_total": "Авто: runtime",
}

ANTI_FLOOD_KEY_RU: dict[str, str] = {
    "ANTI_FLOOD_ENABLED": "Антифлуд включён",
    "MAX_MSG_PER_10S": "Макс. сообщений за 10 сек",
    "MAX_SAME_TEXT": "Повтор одного текста (порог)",
    "MAX_CMD_PER_10S": "Макс. команд за 10 сек",
    "GROUP_COOLDOWN_SEC": "Пауза между ответами в группе (сек)",
}

P95_LABEL_RU: dict[str, str] = {
    "telegram_pipeline": "Telegram → ответ",
    "openrouter_completion": "Модель (AI)",
}

PLANNER_TAG_RU: dict[str, str] = {
    "safe": "безопасный режим",
    "fallback": "запасной маршрут",
    "maint": "самообслуживание",
}

LLM_KIND_RU: dict[str, str] = {
    "chat": "текст / чат",
    "vision": "изображение",
    "audio": "аудио",
    "tool": "инструмент",
    "general": "общий",
}


logger = logging.getLogger(__name__)

def ru_status(val: Any) -> str:
    if val is None:
        return "—"
    s = str(val).strip().lower()
    return STATUS_VALUE_RU.get(s, str(val))


def system_status_lamp(orchestrator: Any) -> str:
    """🟢 / 🟡 / 🔴 для сводок в Telegram (старт, дайджест)."""
    try:
        info = orchestrator.get_system_info()
    except Exception:
        return "🔴"
    if info.get("error"):
        return "🔴"
    overall = str(info.get("overall_status", "")).strip().lower()
    if overall in ("failed", "degraded"):
        return "🔴"
    try:
        rc = getattr(orchestrator, "_resilience", None)
        if rc is not None and rc.is_enabled() and rc.is_safe_mode():
            return "🟡"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'report_i18n', e, exc_info=True)
    if overall in ("healthy", "ok", "full"):
        return "🟢"
    return "🟡"


def format_metrics_table_pre(
    rows: List[Tuple[str, int]],
    *,
    label_max: int = 22,
    num_width: int = 6,
) -> str:
    """
    Моноширинный блок для Telegram <pre>: подпись │ число (число вправо).
    Разделитель визуально отделяет метрику от значения (не «слипается»).
    """
    if not rows:
        return ""

    def _trunc(s: str) -> str:
        t = (s or "").strip()
        if len(t) <= label_max:
            return t
        return t[: max(1, label_max - 1)] + "…"

    displays = [_trunc(str(lbl)) for lbl, _ in rows]
    w = max(len(d) for d in displays) if displays else 0
    out: List[str] = []
    for disp, (_, cnt) in zip(displays, rows):
        try:
            n = int(cnt)
        except (TypeError, ValueError):
            n = 0
        out.append(f"{disp.ljust(w)} │ {n:>{num_width}}")
    return "\n".join(out)


def format_kv_table_pre(
    rows: List[Tuple[str, str]],
    *,
    label_max: int = 26,
    value_max: int = 18,
) -> str:
    """Два текстовых столбца: подпись │ значение (антифлуд, health, сводки).

    Значения выравниваются влево сразу после │ — иначе при смеси коротких чисел и
    длинной строки (например статус модулей) rjust по ширине колонки разъезжает строки.
    """
    if not rows:
        return ""

    def _trunc(s: str, m: int) -> str:
        t = (s or "").replace("\n", " ").replace("\r", " ").strip()
        if len(t) <= m:
            return t
        return t[: max(1, m - 1)] + "…"

    labs = [_trunc(str(a), label_max) for a, _ in rows]
    vals = [_trunc(str(b), value_max) for _, b in rows]
    w = max(len(x) for x in labs) if labs else 0
    out: List[str] = []
    for lb, vl in zip(labs, vals):
        out.append(f"{lb.ljust(w)} │ {vl}")
    return "\n".join(out)


def format_ms_whole(val: Any) -> str:
    """Миллисекунды для UI: целое число без дробной части."""
    try:
        if val is None:
            return "0"
        return str(int(round(float(val))))
    except (TypeError, ValueError):
        return str(val if val is not None else "0")


def ru_bool(val: Any) -> str:
    """True/False для отображения в Telegram."""
    if val is True:
        return "да"
    if val is False:
        return "нет"
    s = str(val).strip().lower()
    if s in ("true", "1", "yes"):
        return "да"
    if s in ("false", "0", "no"):
        return "нет"
    return str(val)


# Подписи компонентов в runtime_errors.jsonl (для оператора)
RUNTIME_COMPONENT_RU: dict[str, str] = {
    "brain": "Мозг (LLM)",
    "anti_flood": "Антифлуд",
    "resilience": "Устойчивость",
    "llm_tiered": "Каскад LLM",
    "task_worker": "Воркер",
    "document_intake": "Документы",
    "voice": "Голос",
    "input_layer": "Вход Telegram",
    "connectivity": "Проверка сети",
    "mem0": "Память Mem0",
    "openrouter": "OpenRouter",
}


def runtime_component_label_ru(component: str) -> str:
    c = (component or "").strip()
    return RUNTIME_COMPONENT_RU.get(c, c.replace("_", " ") or "—")


RUNTIME_SEVERITY_RU: dict[str, str] = {
    "error": "ошибка",
    "warning": "предупреждение",
    "info": "инфо",
    "critical": "критично",
}


def monitor_label_ru(key: str) -> str:
    if not key:
        return ""
    return MONITOR_KEY_RU.get(key, key.replace("_", " "))


def anti_flood_label_ru(key: str) -> str:
    return ANTI_FLOOD_KEY_RU.get(key, key.replace("_", " "))


def p95_label_ru(key: str) -> str:
    return P95_LABEL_RU.get(key, key.replace("_", " "))


def planner_tags_ru(tags: list[str]) -> str:
    out: list[str] = []
    for t in tags:
        out.append(PLANNER_TAG_RU.get(t, t))
    return ", ".join(out)


def llm_kind_ru(kind: str) -> str:
    k = (kind or "").strip().lower()
    return LLM_KIND_RU.get(k, kind or "—")


def format_top_counters_chart_html(counters: Dict[str, Any], *, limit: int = 16) -> str:
    """
    Компактная «диаграмма»: топ счётчиков + полоски █░ относительно максимума в выборке.
    """
    items: list[tuple[str, int]] = []
    for k, v in counters.items():
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        items.append((str(k), iv))
    items.sort(key=lambda x: -abs(x[1]))
    if not items:
        return ""
    top = items[: max(1, limit)]
    mx = max(abs(x[1]) for x in top) or 1
    width = 12
    table_body = format_metrics_table_pre(
        [(monitor_label_ru(k), v) for k, v in top],
        label_max=22,
        num_width=7,
    )
    inner: list[str] = [
        "<i>Таблица — значения; полоски — доля от максимума в этом топе.</i>",
        "",
        f"<pre>{html.escape(table_body)}</pre>",
        "",
    ]
    for k, v in top:
        wbar = max(1, min(width, int(round(width * (abs(v) / mx)))))
        bar = "█" * wbar + "░" * (width - wbar)
        inner.append(f"<code>{html.escape(bar)}</code>")
    return "\n".join(
        [
            "📊 <b>Крупнейшие счётчики</b>",
            "",
            "<blockquote>",
            *inner,
            "</blockquote>",
        ]
    )


REPORT_GLOSSARY_FOOTER_HTML = (
    "<blockquote><b>Коротко о терминах</b>\n"
    "• <code>p95</code> — задержка «хуже, чем у 95%» запросов (мс): типичный «длинный» ответ.\n"
    "• <code>intent</code> — класс запроса для маршрутизации (куда пошёл диалог).\n"
    "• <code>fallback</code> — запасной маршрут, если основной навык не подошёл.\n"
    "• <code>safe mode</code> — безопасный режим: часть функций отключена из‑за ошибок.\n"
    "• <code>токены</code> — единицы текста для LLM; prompt — ваш запрос, completion — ответ модели.\n"
    "• <code>cost</code> — оценка стоимости ответа в USD по данным API.\n"
    "• Англ. имена счётчиков в JSON: <code>/admin_stats_json</code>.</blockquote>"
)
