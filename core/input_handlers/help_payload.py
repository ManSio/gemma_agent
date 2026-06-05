from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.help_catalog_sync import HELP_STATS_ACTIONS, build_help_admin_actions
from core.plugin_registry import PluginRegistry
from core.telegram_ui import esc

# Полный список admin-команд (база + авто-скан хендлеров).
HELP_ADMIN_ACTIONS: List[Tuple[str, str]] = build_help_admin_actions()

def _c(cmd: str) -> str:
    """Одна slash-команда в <code> (parse_mode=HTML)."""
    return f"<code>{esc(cmd.strip())}</code>"


def _build_help_user_actions() -> List[Tuple[str, str]]:
    """Кнопки раздела «Вы»: публичные команды из CORE_COMMANDS + частые команды плагинов."""
    from core.command_catalog import CORE_COMMANDS

    rank = {"basic": 0, "profile": 1, "goals": 2, "general": 3}
    pub = [s for s in CORE_COMMANDS if s.visibility == "public"]
    pub.sort(key=lambda s: (rank.get(s.group, 5), s.token))
    out: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for spec in pub:
        cmd = f"/{spec.token}"
        if cmd in seen:
            continue
        seen.add(cmd)
        lbl = (spec.label or spec.token).strip() or spec.token
        out.append((cmd, lbl))
    plugin_quick = [
        ("/imagine", "Сгенерировать картинку"),
        ("/personas", "Режимы персонажа"),
        ("/get_persona", "Какой персонаж сейчас"),
        ("/set_persona", "Сменить персонажа"),
        ("/explain", "Объяснить тему (школа)"),
        ("/zip_read", "Чтение bundle.json из ZIP"),
    ]
    for cmd, lbl in plugin_quick:
        if cmd not in seen:
            seen.add(cmd)
            out.append((cmd, lbl))
    return out


# (команда, подпись на кнопке) — callback hu:<индекс>
HELP_USER_ACTIONS: List[Tuple[str, str]] = _build_help_user_actions()

# Быстрые админ-команды латок — callback hp:<индекс>
# Текст раздела /help → «Картинки» (генерация и перерисовка по фото).
HELP_IMAGES_LINES: List[str] = [
    "🖼 <b>Картинки — генерация и перерисовка</b>",
    "",
    "<blockquote>",
    "<b>Только текст</b>",
    "«сгенерируй картинку …» или " + f"{_c('/imagine')} <i>описание</i>",
    "",
    "<b>Лучший способ — фото с подписью</b>",
    "Одно сообщение: фото + «перерисуй в аниме» или «сделай как мультик».",
    "",
    "<b>2–3 фото подряд, потом текст</b>",
    "Порядок важен: 1-е фото = первое отправленное. Примеры: «замени фон со 2-го», "
    "«перенеси человека с 1-го на 2-е».",
    "",
    "<b>Сначала фото, потом текст</b>",
    "Фото без подписи → «Фото принял…» → затем инструкция (подождите 1–2 с).",
    "",
    "<b>Третье фото с подписью</b>",
    "Можно: два фото без текста, третье с подписью «совмести / замени фон».",
    "",
    "<b>Только текст «перерисуй» без фото</b>",
    "Не сработает — сначала изображение.",
    "",
    "«Что на фото?» — описание (vision), не генератор.",
    "</blockquote>",
]

HELP_PATCH_ACTIONS: List[Tuple[str, str]] = [
    ("/list_patches", "Список латок"),
    ("/clear_all_patches", "Сбросить все латки"),
    ("/pending_suggested_patch", "Очередь предложений"),
    ("/export_patches", "Экспорт латок"),
    ("/remember_patch", "Добавить латку"),
    ("/forget_patch", "Отключить по id"),
]

# HELP_ADMIN_ACTIONS — см. build_help_admin_actions() выше


def _admin_help_section_key(page: str) -> Optional[str]:
    """Ключ текста раздела справки админа или None."""
    if page == "admin":
        return "overview"
    if page == "admin_stats_page":
        return "stats"
    if page == "admin_obs_2":
        return "obs2"
    if page.startswith("admin_"):
        suf = page[6:]
        if suf in ("sys", "obs", "obs2", "pol", "net", "dev", "bak"):
            return suf
        return "overview"
    return None


# Тексты подстраниц /help → Админ (только навигация help:admin_* — без запуска команд с кнопок).
ADMIN_HELP_LINES: Dict[str, List[str]] = {
    "overview": [
        "🛡️ <b>Админ</b>",
        "",
        "<blockquote>",
        f"{_c('/admin')} — панель с краткими превью",
        f"{_c('/admin_system')} — полная сводка · JSON: {_c('/admin_system_json')}",
        "",
        "Разделы ниже — справочник команд <i>(без запуска с кнопок)</i>.",
        "Быстрые отчёты: «⭐ Статистика» или кнопки в панели /admin.",
        "</blockquote>",
    ],
    "sys": [
        "🖥️ <b>Админ · система и консоль</b>",
        "",
        f"{_c('/admin')} — меню кнопками",
        f"{_c('/admin_system')} — полная сводка",
        _c("/admin_system_json"),
        f"{_c('/admin_health')} — единый health",
        _c("/admin_health_json"),
        f"{_c('/admin_operator')} — консоль оператора <i>(конфиг, STT…)</i>",
        _c("/admin_operator_json"),
    ],
    "obs": [
        "📈 <b>Админ · метрики</b> <i>(1/2)</i>",
        "",
        f"{_c('/admin_stats')} — счётчики мониторинга",
        _c("/admin_stats_json"),
        f"{_c('/admin_llm_usage')} — токены, cost, тренды <i>(см. отчёт)</i>",
        _c("/admin_llm_usage_json"),
        f"{_c('/admin_llm_usage_reset')} confirm — очистить llm_usage.jsonl",
        f"{_c('/admin_kv_debug')} — session-stickiness: session_id, bucket, cached_tok, rolling cache; в JSON — "
        f"<code>prompt_breakdown</code> (секции промпта), <code>agent_pack</code> (full|chat и вставки)",
        _c("/admin_kv_debug_json"),
        f"{_c('/admin_kv_branches')} — ветки Agent KV · {_c('/admin_kv_rollback')} &lt;ns&gt; &lt;key&gt; &lt;ver&gt; "
        f"· {_c('/admin_kv_copy_branch')} &lt;from&gt; &lt;to&gt;",
        f"{_c('/admin_grim_state')} · {_c('/admin_grim_state_json')} · "
        f"{_c('/admin_reputation')} · {_c('/admin_reputation_json')} · "
        f"{_c('/admin_reputation_reset')} &lt;key&gt; "
        f"— CDC/репутация (маршрут <code>user|module|intent</code>, скилл <code>user|skill</code>)",
        f"{_c('/admin_learning_digest')} · {_c('/admin_learning_digest_json')} — дайджест обучения",
        f"{_c('/admin_route_risk_clusters')} · {_c('/admin_route_risk_clusters_json')} — кластеры route_risk",
        f"{_c('/admin_self_model')} · {_c('/admin_self_model_json')} — само-модель агента",
        f"{_c('/admin_session_task')} · {_c('/admin_session_task_json')} — последний ход сессии",
        f"{_c('/admin_mce_status')} · {_c('/admin_mce_status_json')} — Meta-Cognitive Engine",
        f"{_c('/admin_housekeeping')} · {_c('/admin_housekeeping_json')} — обслуживание хранилищ/журналов",
        f"{_c('/admin_efficiency')} — эффективность: экономия токенов, успех плагинов, качество маршрутизации",
        _c("/admin_efficiency_json"),
        f"{_c('/admin_plugins_health')} — здоровье плагинов: загрузка, slash-команды, конфликты",
        _c("/admin_plugins_health_json"),
        f"{_c('/admin_pulse')} — пульс и решения планировщика",
        _c("/admin_pulse_json"),
        f"{_c('/diag')} — краткая сводка: старт, флаги прод, хвост журналов <i>(без ZIP)</i>",
        _c("/admin_diag"),
        f"{_c('/session_trim')} [N] [kv] [slots] — обрезка recent/summary <i>(факты не трогаем)</i>",
        _c("/admin_session_trim"),
        f"{_c('/admin_xray')} — аномалии и узкие места",
        _c("/admin_xray_json"),
        f"{_c('/admin_usage_digest')} — дайджест привычек",
        _c("/admin_usage_digest_json"),
        f"{_c('/admin_turns')} [N] [issues] — журнал ходов turns.jsonl (profile, lane, gate, topic)",
    ],
    "obs2": [
        "📈 <b>Админ · метрики</b> <i>(2/2)</i>",
        "",
        f"{_c('/admin_memory_insight')} [N] — хвост JSONL: strategy_paths, route_risk, experience; N 1–80",
        _c("/admin_memory_insight_json"),
        f"{_c('/admin_reasoning_quality')} — quality reasoning: финальный ответ, завершённость, anti-meta",
        _c("/admin_reasoning_quality_json"),
    ],
    "pol": [
        "🔐 <b>Админ · доступ, данные, антифлуд</b>",
        "",
        f"{_c('/admin_access')} — модерация заявок в личку <i>(кнопки)</i>",
        f"{_c('/admin_governance')} — хранение данных, ретеншн",
        f"{_c('/admin_toggle_skill')} <i>имя</i> — вкл/выкл навыка в brain",
        f"{_c('/admin_plugin_disable')} <i>plugin_name</i> — отключить модуль в реестре",
        f"{_c('/admin_plugin_delete')} <i>plugin_name</i> [force] — удалить из реестра и с диска "
        "<i>(без force — только user_requested_plugin*)</i>",
        f"{_c('/admin_anti_flood')} — параметры антифлуда",
        f"{_c('/admin_group_mode')} on|off — активность в группах: on = бот отвечает без упоминания",
        f"{_c('/admin_group_memory')} <i>4..40</i> — сколько последних сообщений группы держать в краткой памяти",
    ],
    "net": [
        "🌐 <b>Админ · сеть и диагностика</b>",
        "",
        f"{_c('/admin_connectivity')} — Telegram, OpenRouter, Mem0 <i>(~20 с)</i>",
        _c("/admin_connectivity_json"),
        f"{_c('/admin_diagnostic')} — ZIP <i>(boot, perf, логи, снимок)</i>",
        f"{_c('/admin_diagnostic')} net — то же + проверка сети внутри архива",
        f"{_c('/admin_bug')} — <b>реплай на проблемное сообщение</b>: compact ZIP (incident_context + summary + логи), копия на сервере <code>data/diagnostics/bug_reports/</code> · то же по фразе <code>зафиксируй баг</code>",
        f"{_c('/admin_bug')} full — добавить полный <code>bundle.json</code>; {_c('/admin_bug')} net — с сетью; {_c('/admin_bug')} 60 · {_c('/admin_bug')} comp=voice заметка",
        f"{_c('/admin_bug help')} — короткая встроенная инструкция с шаблоном",
        f"{_c('/admin_code_map')} — карта .py, история, дрифт к эталону",
        f"{_c('/admin_code_map_json')} · {_c('/admin_code_baseline_set')} · {_c('/admin_code_baseline_diff_json')}",
        f"{_c('/admin_git')} — commit → push → pull",
    ],
    "dev": [
        "🧰 <b>Админ · устойчивость и журнал</b>",
        "",
        f"{_c('/admin_resilience')} — safe mode, KPI, нагрузка",
        _c("/admin_resilience_json"),
        f"{_c('/admin_plugins_health')} — аудит плагинов: кто загружен, какие команды видны роутеру",
        _c("/admin_plugins_health_json"),
        f"{_c('/admin_logs')} [N] [component] — хвост журнала <i>(новые сверху)</i>, N 1–100; пример: {_c('/admin_logs 50 voice')}",
        f"{_c('/admin_purge_logs')} — очистка по RETENTION_LOG_DAYS",
        f"{_c('/admin_purge_logs')} all — полный сброс журнала; при активном safe mode снимает его",
        f"{_c('/admin_facts')} <i>user_id</i> — факты пользователя",
    ],
    "bak": [
        "💾 <b>Админ · бэкапы, паспорт, автономия</b>",
        "",
        f"{_c('/admin_backup_list')} · {_c('/admin_backup_list_json')}",
        f"{_c('/admin_backup_run')} <i>метка</i> — снять бэкап",
        f"{_c('/admin_restore')} latest | backup_id — восстановление",
        f"{_c('/admin_passport')} — просмотр",
        _c("/admin_passport_json"),
        f"{_c('/admin_passport_set')} <i>json</i> — запись <i>(частичный merge)</i>",
        "",
        "🤖 <b>Автономия</b> <i>(подсказки)</i>",
        _c("/auto_suggestions"),
        f"{_c('/auto_idea')} <i>тема</i>",
        _c("/auto_review"),
        "",
        "⚡ <b>Событийная шина и самовосстановление</b>",
        "EventBus v2 — async-шина событий, соединяющая компоненты:",
        "• module.executed / module.failed — счётчик падений модулей",
        "• bug_report.collected — авто-диагностика через BugContextGatherer при 🐛 Баг",
        "• anomaly.detected — эскалация в safe mode при N аномалий",
        "• maintenance.tick — запуск maintenance цикла",
        "• openrouter.done — AutoLatencyHealer (p95) + AutoFailRatioHealer",
        "",
        "🤖 <b>AutoHealers (без команды)</b>",
        "• AutoLatencyHealer: p95 LLM > HEALER_LATENCY_P95_THRESHOLD_MS → auto-setenv MODEL_SWITCH_THRESHOLD",
        "• AutoFailRatioHealer: fail ratio > HEALER_FAIL_RATIO_THRESHOLD → emit anomaly",
        "• AutoHostPressureHealer: ресурсы критичны → emit anomaly + отключает тяжёлые модули",
        "• ModuleFailureHealer: N падений > HEALER_MODULE_AUTO_DISABLE_AT → auto-disable модуля",
        f"Статус: {_c('/admin_event_bus_healers')} — счётчики, p95, срабатывания, отключённые модули",
        "",
        f"{_c('/admin_event_bus_history')} — хвост событий",
        f"{_c('/admin_event_bus_healers')} — состояние healers: счётчики, патчи",
        f"{_c('/admin_bug_self_heal')} — ретроспектива лечения",
        "",
        "🧠 <b>LLM Triage (диагностика + рекомендации)</b>",
        "• Авто-анализ событий healers через LLM",
        f"{_c('/admin_bug_heal_triage')} — запустить триаж вручную",
        f"{_c('/admin_bug_heal_list')} — список рекомендаций",
        f"{_c('/admin_bug_heal_apply')} &lt;id&gt; — отметить как применённое",
        f"{_c('/admin_bug_heal_dismiss')} &lt;id&gt; — отклонить",
        "",
        "↩️ <b>Auto-Rollback (Фаза 6)</b>",
        "• UndoLog: JSONL-журнал действий авто-лечения",
        "• AutoRollbackEngine: проверка метрик через verify_window_sec",
        "• AutoLatencyHealer: если p95 не снизился → откат MODEL_SWITCH_THRESHOLD",
        "• ModuleFailureHealer: если ошибки растут → re-enable модуля",
        f"{_c('/admin_undo_log')} — список undo-записей",
        f"{_c('/admin_undo_confirm')} &lt;id&gt; — подтвердить (метрики ок)",
        f"{_c('/admin_undo_rollback')} &lt;id&gt; — принудительный откат",
        "",
        "🧠 <b>Meta-Cognitive Engine (Фаза 7-8)</b>",
        "• Само-синтез: 8 сенсоров → unified self-state",
        "• Drift detection: уроки, уверенность, латентность",
        "• Auto-оптимизация: предложения параметров (env)",
        "• Experiment Runner: A/B тестирование параметров",
        "• Meta-Communication: digest, вопросы, цели",
        f"{_c('/admin_mce_status')} — self-state, дрейфы, эксперименты, цели",
        f"{_c('/admin_mce_ask')} &lt;вопрос&gt; — задать вопрос MCE",
        "",
        "🔧 <b>Code Evolution (Фаза 9)</b>",
        "• Patch Runner: генерация, применение, тесты → git → deploy",
        "• Auto-Optimizer: находит тормозящие функции (p95), генерирует патчи",
        "• Evolution Log: журнал всех изменений кода",
        f"{_c('/admin_patch_list')} — список патчей",
        f"{_c('/admin_patch_status')} &lt;id&gt; — детали патча",
        f"{_c('/admin_patch_rollback')} &lt;id&gt; — откат патча",
        f"{_c('/admin_evol_log')} — журнал эволюции кода",
        "🧪 <b>Reasoning / стратегии (примеры)</b>",
        f"{_c('/solution_explorer')} оптимизировать обработку инцидентов без потери качества",
        f"{_c('/reason_timeline')} 2026-05-07 10:00 | status=OPEN\\n2026-05-07 10:30 | status=CLOSED",
        f"{_c('/reason_fsm')} start=INIT target=CLOSED INIT->OPEN OPEN->READY READY->CLOSED forbid ERROR",
        f"{_c('/reason_consistency')} conditions: must:ready forbid:closed || answer: system is closed",
    ],
    "stats": [
        "📊 <b>Админ · статистика и самообучение</b>",
        "",
        "<blockquote>",
        "Кнопки ниже <b>отправляют команду</b> в чат (как в разделе «Вы»).",
        "Обратная связь пользователя: 👍/👎 под ответом бота.",
        "</blockquote>",
        "",
        f"{_c('/admin_reputation')} — репутация CDC (HTML) · {_c('/admin_reputation_json')}",
        f"{_c('/admin_learning_digest')} — HTML; JSON: {_c('/admin_learning_digest_json')}",
        f"{_c('/admin_route_risk_clusters')} — HTML; JSON: {_c('/admin_route_risk_clusters_json')}",
        f"{_c('/admin_memory_insight')} · {_c('/admin_efficiency')} · {_c('/admin_mce_status')}",
        f"{_c('/admin_autonomy')} · {_c('/admin_pulse')} · {_c('/admin_stats')}",
    ],
}


def admin_stats_button_rows() -> List[List[InlineKeyboardButton]]:
    """Быстрые отчёты: callback hs:N → отправка slash-команды в чат."""
    rows: List[List[InlineKeyboardButton]] = []
    _append_paired_callbacks(rows, HELP_STATS_ACTIONS, "hs")
    return rows


def admin_help_section_nav_rows() -> List[List[InlineKeyboardButton]]:
    """Только переходы help:admin_* — одно сообщение перерисовывается через обработчик help:."""
    pairs = [
        ("📊 Система", "admin_sys"),
        ("📈 Метрики", "admin_obs"),
        ("⭐ Статистика", "admin_stats_page"),
        ("🛡️ Доступ", "admin_pol"),
        ("🌐 Сеть", "admin_net"),
        ("🧰 Код", "admin_dev"),
        ("💾 Бэкапы", "admin_bak"),
    ]
    rows: List[List[InlineKeyboardButton]] = []
    pair: List[InlineKeyboardButton] = []
    for label, slug in pairs:
        pair.append(InlineKeyboardButton(text=label, callback_data=f"help:{slug}"))
        if len(pair) >= 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="📑 К разделам админа", callback_data="help:admin")])
    return rows


def admin_commands_panel_rows(*, include_dashboard_back: bool = False) -> List[List[InlineKeyboardButton]]:
    """Клавиатура для /admin → Команды: те же переходы, что в справке (без ac:/ha:)."""
    rows = admin_help_section_nav_rows()
    if include_dashboard_back:
        rows.append([InlineKeyboardButton(text="◀️ Панель /admin", callback_data="admin:dashboard")])
    return rows


def _append_paired_callbacks(
    rows: List[List[InlineKeyboardButton]],
    actions: Sequence[Tuple[str, str]],
    prefix: str,
) -> None:
    pair: List[InlineKeyboardButton] = []
    for i, (_cmd, label) in enumerate(actions):
        text = label if len(label) <= 40 else (label[:37] + "…")
        pair.append(InlineKeyboardButton(text=text, callback_data=f"{prefix}:{i}"))
        if len(pair) >= 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)


def collect_command_catalog(plugin_registry: PluginRegistry) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    modules = plugin_registry.get_modules()
    for mod in modules:
        manifest = getattr(mod, "manifest", None)
        if not manifest:
            continue
        module_name = str(getattr(manifest, "name", getattr(mod, "name", "module")))
        commands = getattr(manifest, "commands", []) or []
        for c in commands:
            if isinstance(c, str):
                cmd = c.strip()
                if not cmd:
                    continue
                trigger = cmd if cmd.startswith("/") else f"/{cmd}"
                rows.append({"trigger": trigger, "description": "", "module": module_name})
            elif isinstance(c, dict):
                trigger = str(c.get("trigger") or c.get("name") or c.get("command") or "").strip()
                if not trigger:
                    continue
                if not trigger.startswith("/"):
                    trigger = f"/{trigger.lstrip('/')}"
                desc = str(c.get("description") or "").strip()
                rows.append({"trigger": trigger, "description": desc, "module": module_name})
    seen = set()
    out: List[Dict[str, str]] = []
    for r in rows:
        key = r["trigger"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def format_telegram_command_catalog_for_brain(
    plugin_registry: PluginRegistry,
    *,
    tier: str = "full",
    max_chars: int = 14000,
    max_module_commands: int = 160,
) -> str:
    """
    Один источник правды для списка slash-команд: ядро + манифесты модулей.
    tier: minimal | full — см. format_brain_telegram_command_catalog.
    """
    from core.command_catalog import format_brain_telegram_command_catalog

    return format_brain_telegram_command_catalog(
        plugin_registry,
        tier=tier,
        max_chars=max_chars,
        max_module_commands=max_module_commands,
    )


def build_help_payload(
    *,
    plugin_registry: PluginRegistry,
    is_admin: bool,
    page: str = "main",
) -> tuple[List[str], Optional[InlineKeyboardMarkup]]:
    page = (page or "main").lower()
    lines: List[str] = []
    modules_page_idx = 1
    if page.startswith("modules_"):
        try:
            modules_page_idx = max(1, int(page.split("_", 1)[1]))
        except Exception:
            modules_page_idx = 1
        page = "modules"

    admin_key = _admin_help_section_key(page)

    if admin_key is not None:
        if not is_admin:
            lines = [
                "🛡️ <b>Раздел «Админ»</b>",
                "",
                "<blockquote>",
                "Доступ только администраторам: ваш Telegram ID должен быть в",
                f"{_c('ADMIN_USER_IDS')} или {_c('ADMIN_NOTIFY_USER_IDS')} в .env",
                "</blockquote>",
            ]
        else:
            raw_admin = list(ADMIN_HELP_LINES[admin_key])
            if len(raw_admin) >= 2 and raw_admin[1] == "":
                lines = [raw_admin[0], raw_admin[1], "<blockquote>", *raw_admin[2:], "</blockquote>"]
            else:
                lines = raw_admin
    elif page == "images":
        lines = list(HELP_IMAGES_LINES)
    elif page == "user":
        lines = [
            "👤 <b>Вы и бот</b> — основное",
            "",
            "<blockquote>",
            f"{_c('/start')} · {_c('/help')} · {_c('/geo_help')} · {_c('/id')}",
            f"{_c('/me')} · {_c('/chat_style')} · {_c('/facts')} · {_c('/forget')} <i>поле</i>",
            f"{_c('/status')} · {_c('/plugins')} · {_c('/calc')} <i>2+2</i> · {_c('/note')}",
            f"{_c('/rate +1')} · {_c('/rate -1')} · {_c('/correct')} — обратная связь",
            "",
            "Учёба: " + f"{_c('/explain')} · {_c('/solve')} · {_c('/check')} · {_c('/quiz')}",
            f"Картинки: {_c('/imagine')} · «сгенерируй картинку…» · фото + «перерисуй…» — см. «🖼 Картинки»",
            "</blockquote>",
            "",
            "<i>Подробный список — кнопка «Ещё»; картинки — «🖼 Картинки»; команды модулей — «Плагины».</i>",
        ]
    elif page == "user_more":
        lines = [
            "👤 <b>Вы и бот</b> — полный список",
            "",
            "<blockquote>",
            "Ниже — текстом; часть продублирована <b>кнопками</b> <i>(как в разделе «Модули»)</i>.",
            "</blockquote>",
            "",
            "🚀 <b>Начало</b>",
            "<blockquote>",
            f"{_c('/start')} — приветствие",
            f"{_c('/help')} — эта справка",
            f"{_c('/geo_help')} — карты: кнопка «поделиться геолокацией» и памятка",
            f"{_c('/id')} — ваш числовой Telegram ID <i>(для списка админов в настройках сервера)</i>",
            "</blockquote>",
            "",
            "🪪 <b>Профиль</b>",
            "<blockquote>",
            f"{_c('/me')} — сводка: факты, настройки, психопрофиль, двойник",
            f"{_c('/chat_style')} — <b>как бот отвечает</b>: баланс, простой чат без «зануды», умные темы, коротко, теплее "
            f"<i>(кнопки; то же: {_c('/style')})</i>",
            f"{_c('/facts')} — сохранённые факты о вас",
            f"{_c('/psych')} — эвристический психопрофиль <i>(тональность, стресс-маркеры)</i>",
            f"{_c('/twin')} — цифровой двойник <i>(локация, учёба, интересы)</i>",
            f"{_c('/forget')} <i>поле</i> — удалить один факт",
            f"{_c('/facts_refresh')} — обновить факты с диска",
            f"{_c('/facts_reset')} — сбросить все факты",
            f"{_c('/new')} — новый диалог (сброс recent, новая эпоха KV)",
            "</blockquote>",
            "",
            "🎓 <b>Учёба (School Assistant)</b>",
            "<blockquote>",
            f"{_c('/explain')} <i>предмет тема</i> — объяснение темы",
            f"{_c('/solve')} <i>предмет задача</i> — пошаговое решение (strict-режим для математики)",
            f"{_c('/check')} <i>предмет задача || ответ</i> — проверка ответа",
            f"{_c('/quiz')} <i>предмет тема</i> — мини-тест по теме",
            "</blockquote>",
            "",
            "💾 <b>Система и память</b>",
            "<blockquote>",
            f"{_c('/status')} — то же, что {_c('/system_state')}: модули, антифлуд, знания",
            f"{_c('/system_state')} — полное имя команды статуса",
            f"{_c('/plugins')} — все плагины в реестре: 🟢 ок · 🟡 ошибка · 🔴 выключен",
            f"{_c('/plugins_help')} — что означают индикаторы и куда смотреть дальше",
            f"{_c('/get_mem0_facts')} — факты из облачной памяти Mem0 <i>(если включено)</i>",
            f"{_c('/filefrom')} <i>https://…</i> — скачать файл и отправить в чат <i>(как UrlFetch для файла)</i>",
            f"{_c('/corpus_books')} — список книг в корпусе <i>(смещение: {_c('/corpus_books')} 80)</i>",
            f"{_c('/corpus_docs')} — список документов <i>(НПА, общая база…; пагинация как у книг)</i>",
            f"{_c('/corpus_doc')} <i>book:… | law:…</i> — оригинал из корпуса",
            f"{_c('/calc')} <i>выражение</i> — калькулятор <i>(латиница, операторы + - * / ^)</i>",
            f"{_c('/note')} <i>текст</i> — сохранить личную заметку",
            "</blockquote>",
            "",
            "🎯 <b>Многошаговая цель</b> <i>(если в .env включён GOAL_RUNNER_ENABLED)</i>",
            "<blockquote>",
            f"{_c('/goal_run')} <i>формулировка</i> · {_c('/goal_step')} · {_c('/goal_status')} · {_c('/goal_cancel')}",
            "</blockquote>",
            "",
            "👍 <b>Обратная связь</b>",
            "<blockquote>",
            "Под ответом бота — кнопки «Хороший ответ» / «Плохой ответ» / «Баг».",
            f"{_c('/rate +1')} · {_c('/rate -1')} [замечание] — оценка последнего ответа.",
            f"{_c('/correct')} — реплай с поправкой или текст после команды.",
            "Учитывается в experience, репутации (CDC) и ephemeral-уроках.",
            "</blockquote>",
            "",
            "🧰 <b>Утилиты (плагин tools)</b>",
            "<blockquote>",
            f"{_c('/zip_read')} bundle.json — чтение диагностического ZIP; см. также {_c('/read_file')}, {_c('/zip_list')}",
            "</blockquote>",
            "",
            "🖼 <b>Генерация картинок</b> — подробно в /help → «🖼 Картинки»",
            "<blockquote>",
            f"{_c('/imagine')} <i>описание</i> · «сгенерируй картинку …»",
            "Фото + «перерисуй …» · сначала фото, потом текст с запросом.",
            "«Что на фото?» — описание, не генерация.",
            "</blockquote>",
        ]
        if (os.getenv("BUG_REPORT_USER_SUBMIT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}:
            lines.extend(
                [
                    "",
                    "🐞 <b>Сообщить о сбое</b>",
                    "<blockquote>",
                    f"{_c('/bug')} — в <b>личке</b>: отчёт разработчику; архив с диагностикой вам <b>не</b> присылается, "
                    "его получают только администраторы. Лучше ответить <b>реплаем</b> на проблемное сообщение бота.",
                    "Также в начале строки: <code>Зафиксируй баг</code> <i>(те же опции в хвосте, что у /admin_bug)</i>.",
                    "</blockquote>",
                ]
            )
    elif page == "modules":
        lines = []
    elif page == "patches":
        if not is_admin:
            lines = [
                "🩹 <b>Раздел «Латки»</b>",
                "",
                "<blockquote>",
                "Доступ только администраторам <i>(ADMIN_USER_IDS / ADMIN_NOTIFY_USER_IDS в .env)</i>.",
                "</blockquote>",
            ]
        else:
            lines = [
                "🩹 <b>Латки</b> — эфемерные правила без деплоя кода",
                "",
                "<blockquote>",
                f"Подсказка: {_c('/remember_patch')} триггер || инструкция [ || force_general ]",
                f"Пример: {_c('/remember_patch')} t.me/+ || не предлагай /calc, приглашение || force_general",
                "",
                f"{_c('/remember_patch')} — добавить латку",
                f"{_c('/forget_patch')} <i>id</i> — отключить по id из {_c('/list_patches')}",
                f"{_c('/clear_all_patches')} — отключить все латки; "
                f"<code>full</code> — ещё и очередь; <code>queue</code> — только очередь",
                f"{_c('/list_patches')} — активные латки",
                f"{_c('/export_patches')} — выгрузка для Cursor/бэкапа",
                f"{_c('/pending_suggested_patch')} — очередь предложений от пользователей",
                f"{_c('/approve_suggested_patch')} <i>id</i> · {_c('/dismiss_suggested_patch')} <i>id</i>",
                "</blockquote>",
            ]
    else:
        main_body: List[str] = [
            "<b>Разделы</b> — кнопки ниже",
            "",
            "• <b>Вы</b> — старт, профиль, обратная связь",
            "• <b>Ещё</b> — полный список команд ядра",
            "• <b>Плагины</b> — команды расширений <i>(постранично)</i>",
        ]
        if is_admin:
            main_body.append("• <b>Админ</b> — панель и отчёты")
            main_body.append(f"• <b>Латки</b> — {_c('/remember_patch')}…")
        main_body.extend(
            [
                "",
                "💬 <b>Без команд</b>: «переведи…», «посчитай 2+2», «напомни в 19:00…», «сгенерируй картинку…», обычный диалог.",
            ]
        )
        lines = [
            "📘 <b>Справка</b>",
            "",
            "<blockquote>",
            *main_body,
            "</blockquote>",
        ]
    catalog = collect_command_catalog(plugin_registry)
    per_page = 20
    total_pages = 1
    if page == "modules":
        if catalog:
            total_pages = max(1, (len(catalog) + per_page - 1) // per_page)
            modules_page_idx = min(modules_page_idx, total_pages)
            start = (modules_page_idx - 1) * per_page
            end = start + per_page
            header = (
                f"🧩 <b>Плагины</b> — команды из manifest <i>(стр. {modules_page_idx}/{total_pages})</i>"
            )
            hint = (
                "Кнопки ниже дублируют команды: нажмите, чтобы отправить слэш-команду в чат "
                "<i>(в группе к команде добавится @ник бота)</i>."
            )
            cmd_lines = [hint]
            for row in catalog[start:end]:
                d = f" — {esc(row['description'])}" if row["description"] else ""
                mod = f" [{esc(row['module'])}]" if row.get("module") else ""
                cmd_lines.append(f"{_c(row['trigger'])}{d}{mod}")
            body = "\n".join(cmd_lines)
            lines = [header, "", f"<blockquote>{body}</blockquote>"]
        else:
            header = "🧩 <b>Плагины</b> — команды расширений"
            body = "\n".join(
                [
                    "Список команд пуст: в manifest плагинов нет <code>commands</code>, либо модули не загрузились.",
                    f"Проверка: {_c('/status')} / {_c('/system_state')} — какие модули активны.",
                    "В группе: слэш-команда с <code>@username_бота</code>, ответ боту или упоминание — иначе бот не увидит сообщение.",
                ]
            )
            lines = [header, "", f"<blockquote>{body}</blockquote>"]

    chunks: List[str] = []
    cur = ""
    for ln in lines:
        add = ln + "\n"
        if len(cur) + len(add) > 3200:
            chunks.append(cur.rstrip())
            cur = add
        else:
            cur += add
    if cur.strip():
        chunks.append(cur.rstrip())

    from core.input_handlers.telegram_nav import help_admin_obs_nav_rows, help_hub_nav_rows

    hub_page = page
    if page == "modules":
        hub_page = f"modules_{modules_page_idx}"
    rows: List[List[InlineKeyboardButton]] = list(help_hub_nav_rows(active=hub_page))
    if is_admin and page not in ("patches",) and not str(page).startswith("admin"):
        rows.append(
            [
                InlineKeyboardButton(text="🩹 Латки", callback_data="help:patches"),
                InlineKeyboardButton(text="⚙️ Админ", callback_data="help:admin"),
            ]
        )
    if page == "modules" and catalog:
        mstart = (modules_page_idx - 1) * per_page
        mend = min(mstart + per_page, len(catalog))
        pair: List[InlineKeyboardButton] = []
        for off, row in enumerate(catalog[mstart:mend]):
            gi = mstart + off
            label = row["trigger"]
            if len(label) > 40:
                label = label[:37] + "…"
            # callback_data ≤ 64 байт: hc: + индекс
            pair.append(InlineKeyboardButton(text=label, callback_data=f"hc:{gi}"))
            if len(pair) >= 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
    if page == "modules" and total_pages > 1:
        pag_row: List[InlineKeyboardButton] = []
        if modules_page_idx > 1:
            pag_row.append(InlineKeyboardButton(text="←", callback_data=f"help:modules_{modules_page_idx - 1}"))
        if modules_page_idx < total_pages:
            pag_row.append(InlineKeyboardButton(text="→", callback_data=f"help:modules_{modules_page_idx + 1}"))
        if pag_row:
            rows.append(pag_row[:2])
    if page == "user":
        _append_paired_callbacks(rows, HELP_USER_ACTIONS[:8], "hu")
    if page == "user_more":
        _append_paired_callbacks(rows, HELP_USER_ACTIONS, "hu")
    if page == "patches" and is_admin:
        _append_paired_callbacks(rows, HELP_PATCH_ACTIONS, "hp")
    if admin_key is not None and is_admin:
        for row in admin_help_section_nav_rows():
            rows.append(row)
        if admin_key == "stats":
            for row in admin_stats_button_rows():
                rows.append(row)
        elif admin_key == "obs":
            rows.extend(help_admin_obs_nav_rows(page=1))
            rows.append(
                [InlineKeyboardButton(text="⭐ Все отчёты", callback_data="help:admin_stats_page")]
            )
        elif admin_key == "obs2":
            rows.extend(help_admin_obs_nav_rows(page=2))
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return chunks, kb
