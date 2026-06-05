"""Синхронизация справки /help с реально зарегистрированными slash-командами."""
from __future__ import annotations

from typing import Dict, List, Tuple

# Базовый упорядоченный список (подписи вручную); дополняется авто-сканом хендлеров.
_HELP_ADMIN_STATIC: Tuple[Tuple[str, str], ...] = (
    ("/admin", "Панель админа"),
    ("/admin_system", "Сводка системы"),
    ("/admin_system_json", "Сводка (JSON)"),
    ("/admin_health", "Здоровье (HTML)"),
    ("/admin_health_json", "Здоровье (JSON)"),
    ("/admin_operator", "Консоль оператора"),
    ("/admin_operator_json", "Оператор (JSON)"),
    ("/admin_seed_runtime", "Сиды из примеров"),
    ("/admin_stats", "Счётчики мониторинга"),
    ("/admin_stats_json", "Счётчики (JSON)"),
    ("/admin_llm_usage", "Расход LLM"),
    ("/admin_llm_usage_json", "Расход LLM (JSON)"),
    ("/admin_llm_usage_reset confirm", "Сброс журнала LLM"),
    ("/admin_autonomy", "Автономность: уроки, эталоны, кеш"),
    ("/rate +1", "Оценить последний ответ"),
    ("/correct", "Поправка ответа (реплай)"),
    ("/admin_learning_digest", "Дайджест обучения"),
    ("/admin_run_learning", "Запуск цикла обучения"),
    ("/admin_route_risk_clusters", "Кластеры route_risk"),
    ("/admin_kv_debug", "Отладка KV-сессии"),
    ("/admin_kv_debug_json", "KV (полный JSON)"),
    ("/admin_kv_branches", "Ветки Agent KV"),
    ("/admin_kv_rollback", "Откат KV-ветки"),
    ("/admin_kv_copy_branch", "Копировать KV-ветку"),
    ("/admin_grim_state", "CDC / grim state"),
    ("/admin_reputation", "Репутация маршрутов и скиллов"),
    ("/admin_reputation_json", "Репутация (JSON)"),
    ("/admin_reputation_reset", "Сброс репутации"),
    ("/admin_self_model", "Самомодель агента"),
    ("/admin_session_task", "Сводка хода: маршрут + tool"),
    ("/admin_router", "Статус LLM-роутера"),
    ("/admin_housekeeping", "Обслуживание хранилищ"),
    ("/admin_housekeeping_json", "Housekeeping JSON"),
    ("/admin_efficiency", "Эффективность токенов/маршрутов"),
    ("/admin_efficiency_json", "Эффективность (JSON)"),
    ("/admin_plugins_health", "Аудит плагинов"),
    ("/admin_plugins_health_json", "Плагины (JSON)"),
    ("/admin_pulse", "Пульс маршрутизации"),
    ("/admin_pulse_json", "Пульс (JSON)"),
    ("/diag", "Краткая сводка (старт, флаги, журналы)"),
    ("/admin_diag", "То же, что /diag"),
    ("/admin_xray", "Рентген узких мест"),
    ("/admin_xray_json", "Рентген (JSON)"),
    ("/admin_usage_digest", "Дайджест активности"),
    ("/admin_usage_digest_json", "Дайджест (JSON)"),
    ("/admin_turns", "Журнал ходов turns.jsonl"),
    ("/admin_memory_insight", "Память маршрутизатора"),
    ("/admin_memory_insight_json", "Память (JSON)"),
    ("/admin_memory_ops", "Memory ops сводка"),
    ("/admin_reasoning_quality", "Качество рассуждений"),
    ("/admin_reasoning_quality_json", "Качество (JSON)"),
    ("/admin_access", "Заявки в личку"),
    ("/admin_governance", "Политика данных"),
    ("/admin_toggle_skill", "Вкл/выкл навыка brain"),
    ("/admin_connectivity", "Проверка сети (~20 с)"),
    ("/admin_connectivity_json", "Сеть (JSON)"),
    ("/admin_diagnostic", "Диагностика (ZIP)"),
    ("/admin_bug", "Отчёт о баге (ZIP)"),
    ("/bug", "Баг-репорт пользователя (личка)"),
    ("/admin_code_map", "Карта исходников"),
    ("/admin_code_map_json", "Карта кода (JSON)"),
    ("/admin_code_baseline_set", "Запомнить эталон кода"),
    ("/admin_code_baseline_diff_json", "Отличия от эталона"),
    ("/admin_git", "Git pipeline: commit + push + pull"),
    ("/admin_resilience", "Устойчивость и safe mode"),
    ("/admin_resilience_json", "Устойчивость (JSON)"),
    ("/admin_logs 40", "Журнал ошибок (40 строк)"),
    ("/admin_purge_logs", "Очистка журнала"),
    ("/admin_anti_flood", "Настройки антифлуда"),
    ("/admin_group_mode on", "Группы: бот активен"),
    ("/admin_group_memory 12", "Память группы (12)"),
    ("/admin_backup_list", "Список бэкапов"),
    ("/admin_backup_list_json", "Бэкапы (JSON)"),
    ("/admin_backup_run quick", "Сделать бэкап"),
    ("/admin_restore latest", "Восстановление из бэкапа"),
    ("/admin_facts 123", "Факты пользователя по ID"),
    ("/admin_plugin_disable demo", "Отключить плагин"),
    ("/admin_plugin_delete user_requested_plugin_6", "Удалить плагин"),
    ("/admin_passport", "Паспорт разработки"),
    ("/admin_passport_json", "Паспорт (JSON)"),
    ("/admin_passport_set", "Запись паспорта (JSON)"),
    ("/auto_suggestions", "Идеи автономии"),
    ("/auto_idea тема", "Идея автономии"),
    ("/auto_review", "Обзор автономии"),
    ("/admin_mce_status", "MCE: self-state и цели"),
    ("/admin_mce_ask вопрос", "MCE: задать вопрос"),
    ("/admin_event_bus_history", "EventBus: история"),
    ("/admin_event_bus_healers", "EventBus: healers"),
    ("/admin_event_bus_subscribers", "EventBus: подписчики"),
    ("/admin_bug_self_heal", "Ретроспектива лечения"),
    ("/admin_bug_heal_triage", "LLM-триаж healers"),
    ("/admin_bug_heal_list", "Список рекомендаций triage"),
    ("/admin_bug_heal_apply", "Применить рекомендацию"),
    ("/admin_bug_heal_dismiss", "Отклонить рекомендацию"),
    ("/admin_undo_log", "Undo-журнал"),
    ("/admin_undo_confirm", "Подтвердить undo"),
    ("/admin_undo_rollback", "Откат undo"),
    ("/admin_patch_list", "Список патчей"),
    ("/admin_patch_status", "Статус патча"),
    ("/admin_patch_rollback", "Откат патча"),
    ("/admin_evol_log", "Журнал эволюции кода"),
    ("/remember_patch", "Добавить латку"),
    ("/forget_patch", "Отключить латку"),
    ("/list_patches", "Список латок"),
    ("/clear_all_patches", "Сбросить латки"),
    ("/export_patches", "Экспорт латок"),
    ("/pending_suggested_patch", "Очередь латок"),
    ("/approve_suggested_patch", "Одобрить латку"),
    ("/dismiss_suggested_patch", "Отклонить латку"),
)

_TOKEN_LABEL_OVERRIDES: Dict[str, str] = {
    "admin_reputation": "Репутация маршрутов и скиллов",
    "admin_learning_digest": "Дайджест обучения",
    "admin_route_risk_clusters": "Кластеры ошибок",
}


def build_help_admin_actions() -> List[Tuple[str, str]]:
    from core.command_catalog import discover_aiogram_command_tokens, find_core_spec, is_admin_command_pattern

    out: List[Tuple[str, str]] = list(_HELP_ADMIN_STATIC)
    seen = {a.split()[0].lstrip("/").lower() for a, _ in out}
    for tok in sorted(discover_aiogram_command_tokens()):
        if not is_admin_command_pattern(tok):
            continue
        if tok in seen:
            continue
        spec = find_core_spec(tok)
        if spec and spec.label:
            label = spec.label
        else:
            label = _TOKEN_LABEL_OVERRIDES.get(tok) or tok.replace("_", " ").replace("admin ", "admin·")
        out.append((f"/{tok}", label[:48]))
        seen.add(tok)
    return out


# Кнопки «Статистика» в /help и /admin — отправляют команду в чат (callback hs:N).
HELP_STATS_ACTIONS: List[Tuple[str, str]] = [
    ("/admin_reputation", "⭐ Репутация скиллов"),
    ("/admin_learning_digest", "🧠 Дайджест обучения"),
    ("/admin_route_risk_clusters", "⚠️ Кластеры ошибок"),
    ("/admin_memory_insight 15", "💾 Память маршрутов"),
    ("/admin_memory_ops 25", "📊 Memory ops"),
    ("/admin_efficiency", "⚡ Эффективность"),
    ("/admin_mce_status", "🎯 MCE статус"),
    ("/admin_autonomy", "📚 Уроки и кеш"),
    ("/admin_pulse", "🫀 Пульс"),
    ("/admin_reasoning_quality", "🧪 Reasoning"),
    ("/admin_stats", "📈 Счётчики"),
    ("/admin_llm_usage", "💰 Расход LLM"),
]
