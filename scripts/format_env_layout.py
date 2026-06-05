#!/usr/bin/env python3
"""
Переупорядочивает .env: блоки ключи → URL → модели → настройки провайдеров → остальное по смыслу.
Использование: python scripts/format_env_layout.py [входной.env] [выходной.env]
По умолчанию: .env .env.out
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# (заголовок секции, список ключей в желаемом порядке)
SECTIONS: list[tuple[str, list[str]]] = [
    (
        "1. Ключи и секреты API (и шифрование приложения)",
        [
            "TELEGRAM_TOKEN",
            "OPENROUTER_API_KEY",
            "OPENROUTER_API_KEY_DEV",
            "MEM0_API_KEY",
            "API_TOKEN",
            "QDRANT_API_KEY",
            "LINK_REPUTATION_API_KEY",
            "ENCRYPTION_KEY",
            "SECURITY_AES_KEY",
            "SECURITY_SALT",
        ],
    ),
    (
        "2. URL, хосты и порты подключения к сервисам",
        [
            "MEM0_API_URL",
            "MEM0_LOCAL",
            "QDRANT_URL",
            "SEARXNG_INSTANCE_URL",
            "IMAGE_GEN_API_URL",
            "OPENROUTER_HTTP_REFERER",
            "LINK_REPUTATION_API_ENDPOINT",
            "API_HOST",
            "API_PORT",
        ],
    ),
    (
        "3. Модели ИИ (имена моделей / embedding)",
        [
            "OPENROUTER_MODEL_FREE",
            "OPENROUTER_MODEL_DEV",
            "OPENROUTER_MODEL_QWEN",
            "OPENROUTER_MODEL_VISION",
            "BRAIN_LLM_FREE_MODEL",
            "BRAIN_LLM_PREMIUM_MODEL",
            "BRAIN_FAST_CHITCHAT_MODEL",
            "IMAGE_GEN_MODEL",
            "IMAGE_GEN_MODEL_FALLBACK",
            "QDRANT_EMBEDDING_MODEL",
            "DEFAULT_MODEL",
            "MODEL_SWITCH_THRESHOLD",
        ],
    ),
    (
        "4. OpenRouter и каскад LLM: HTTP, таймауты, лимиты ответа",
        [
            "OPENROUTER_HTTP_TOTAL_TIMEOUT_SEC",
            "OPENROUTER_HTTP_RETRY_ATTEMPTS",
            "OPENROUTER_HTTP_RETRY_GAP_SEC",
            "OPENROUTER_MODELS_CACHE_SEC",
            "OPENROUTER_LENGTH_FINISH_SUFFIX",
            "OPENROUTER_SESSION_HEADERS_ENABLED",
            "OPENROUTER_X_TITLE",
            "BRAIN_LLM_TIERED_RETRY",
            "BRAIN_LLM_FREE_ATTEMPTS",
            "BRAIN_LLM_FREE_RETRY_GAP_SEC",
            "BRAIN_LLM_WAIT_BEFORE_PREMIUM_SEC",
            "BRAIN_LLM_FREE_TIMEOUT_SEC",
            "BRAIN_LLM_PREMIUM_TIMEOUT_SEC",
            "OP_TIMEOUT_SEC",
            "OP_RETRIES",
            "BRAIN_LLM_TOKENS_PER_SEC_EST",
            "BRAIN_LLM_ETA_LEARN_ENABLED",
            "BRAIN_FIRST_MAX_TOKENS",
            "BRAIN_SECOND_MAX_TOKENS",
        ],
    ),
    (
        "5. HTTP API приложения (api.py)",
        [
            "API_ENABLED",
            "API_HOST",
            "API_PORT",
            "API_RATE_LIMIT_ENABLED",
            "API_RATE_LIMIT_HEAVY_RPM",
            "API_RATE_LIMIT_HEAVY_MIN_INTERVAL_SEC",
            "AGENT_PROBE_HTTP_MIN_INTERVAL_SEC",
            "API_CORS_ENABLED",
            "API_CORS_ORIGINS",
        ],
    ),
    (
        "6. Qdrant: коллекция и смежное",
        [
            "QDRANT_COLLECTION",
        ],
    ),
    (
        "7. Telegram: токен уже в §1; админы, доступ, UX",
        [
            "ADMIN_USER_IDS",
            "ADMIN_NOTIFY_USER_IDS",
            "ADMIN_STARTUP_NOTIFY",
            "USER_ACCESS_APPROVAL_REQUIRED",
            "USER_ACCESS_PENDING_MESSAGE",
            "TELEGRAM_PIPELINE_SERIALIZE_BY_CHAT",
            "TELEGRAM_REPLY_TO_USER_MESSAGE",
            "GRAM_PROGRESS_UI",
        ],
    ),
    (
        "8. Мозг: инструменты, vision, плагины",
        [
            "BRAIN_TOOL_ROUTING_HINT",
            "BRAIN_TOOLS_PRIORITIZE_HINT",
            "BRAIN_TOOL_CALL_RETRY",
            "BRAIN_TOOL_CHAIN_MAX",
            "BRAIN_TOOL_DEDUP_ENABLED",
            "BRAIN_TOOL_DEDUP_TTL_SEC",
            "BRAIN_USER_INPUT_HEAVY_CHAR_THRESHOLD",
            "BRAIN_VISION_TWO_STEP",
            "BRAIN_VISION_FALLBACK_SINGLE_STAGE",
            "BRAIN_IMAGE_SLIM_PROMPT",
            "BRAIN_VISION_PRECAPTION_TIMEOUT_SEC",
            "BRAIN_DEDUP_MEMORY",
            "BRAIN_PLUGIN_AUTHOR_DOCS",
            "BRAIN_POST_MODULE_GEN_BUTTONS",
        ],
    ),
    (
        "9. Планировщик, стратегия, опыт, сессии, риск маршрута",
        [
            "LOOKAHEAD_PLANNER_ENABLED",
            "LOOKAHEAD_MAX_STEPS",
            "LOOKAHEAD_HORIZON",
            "STRATEGY_PATH_HINT_FOR_SHALLOW",
            "STRATEGY_PATH_MEMORY_ENABLED",
            "GEMMA_STRATEGY_PATH",
            "STRATEGY_PATH_MAX_LINES",
            "STRATEGY_PATH_LOOKBACK",
            "EXPERIENCE_MEMORY_ENABLED",
            "EXPERIENCE_HINT_CONF_THRESHOLD",
            "GEMMA_EXPERIENCE_PATH",
            "EXPERIENCE_MAX_LINES",
            "EXPERIENCE_MAX_FILE_BYTES",
            "SESSION_DIGEST_ENABLED",
            "SESSION_DIGEST_EVERY_N_TURNS",
            "SESSION_DIGEST_BUFFER_CHARS",
            "SESSION_DIGEST_MIN_USER_CHARS",
            "GEMMA_SESSION_DIGEST_PATH",
            "ROUTE_RISK_MEMORY_ENABLED",
            "ROUTE_RISK_MIN_STUMBLES",
            "ROUTE_RISK_LOOKBACK_LINES",
            "GEMMA_ROUTE_RISK_PATH",
            "ROUTE_RISK_MAX_LINES",
        ],
    ),
    (
        "10. CDC, Agent KV, GRIM",
        [
            "CDC_ENGINE_ENABLED",
            "CDC_AGG_JSON_MIRROR",
            "CDC_DISABLE_ROUTE_ON_ROUTE_PROBLEM",
            "CDC_CONSUME_NEXT_TIER_FLOOR_ON_USE",
            "AGENT_KV_ENABLED",
            "GRIM_TRIGGER_ENABLED",
        ],
    ),
    (
        "11. Автопилот и usage learning",
        [
            "GEMMA_AUTOPILOT_MODE",
            "AUTOPILOT_CYCLE_INTERVAL_SEC",
            "AUTOPILOT_INNER_TICK_SEC",
            "AUTOPILOT_REPORT_TO_ADMINS",
            "AUTOPILOT_ACTIONS_ENABLED",
            "AUTOPILOT_DIGEST_HOURS_UTC",
            "AUTOPILOT_DIGEST_QUIET_ONLY",
            "AUTOPILOT_IDLE_MIN_SEC",
            "AUTOPILOT_IDLE_LLM_PROBE",
            "AUTOPILOT_QUIET_HOURS_UTC",
            "AUTOPILOT_LLM_PROBE_MIN_INTERVAL_SEC",
            "AUTOPILOT_LLM_PROBE_NOTIFY_ON_FAIL",
            "USAGE_LEARNING_SAVE_EVERY",
        ],
    ),
    (
        "12. Логи и телеметрия",
        [
            "LOG_LEVEL",
            "GEMMA_CORE_LOG_FULL",
            "GEMMA_LLM_AUDIT_LOG",
            "LATENCY_TRACE_LOG",
            "LATENCY_TRACE_SLOW_MS",
            "LIVE_PULSE_PLANNER_TAIL",
            "LIVE_PULSE_TELEGRAM_P95_WARN_MS",
            "LIVE_PULSE_TELEGRAM_P95_CRITICAL_MS",
            "LIVE_PULSE_OPENROUTER_P95_CRITICAL_MS",
        ],
    ),
    (
        "13. Пути, каталоги данных, runtime",
        [
            "MODULES_PATH",
            "CORE_LIBRARIES_PATH",
            "PROJECT_ROOT",
            "RUNTIME_ENSURE_DATA_LAYOUT",
            "RUNTIME_DIR_MODE",
            "PLUGIN_MANIFEST_PATHS",
            "RAG_DATABASE_PATH",
            "CACHE_PATH",
            "MODELS_PATH",
            "BEHAVIOR_DATA_DIR",
            "ERROR_ANALYSIS_DIR",
            "GEMMA_LLM_USAGE_PERSIST",
            "DATABASE_PATH",
            "RESILIENCE_RUNTIME_DIR",
        ],
    ),
    (
        "14. Приложение: окружение, антиабьюз, ссылки",
        [
            "APP_ENV",
            "ANTI_FLOOD_ENABLED",
            "MAX_MSG_PER_10S",
            "MAX_SAME_TEXT",
            "MAX_CMD_PER_10S",
            "GROUP_COOLDOWN_SEC",
            "HARD_FLOOD_MULTIPLIER",
            "ANTI_FLOOD_RESPONSE",
            "LINK_SAFETY_ENABLED",
            "LINK_SAFETY_MODE",
        ],
    ),
    (
        "15. Файлы, изображения, документы, архивы, код",
        [
            "FILE_INTAKE_ENABLED",
            "FILE_MAX_IMAGE_MB",
            "FILE_MAX_DOC_MB",
            "FILE_MAX_AUDIO_MB",
            "FILE_TEMP_DIR",
            "IMAGE_TOOLS_ENABLED",
            "IMAGE_MAX_RESOLUTION",
            "DOC_INTAKE_ENABLED",
            "ZIP_MAX_ENTRIES",
            "ZIP_MAX_UNPACKED_MB",
            "CODE_INTAKE_ENABLED",
            "CODE_INTAKE_MAX_FILE_KB",
        ],
    ),
    (
        "16. Поиск SearxNG",
        [
            "SEARXNG_ENABLED",
        ],
    ),
    (
        "17. Усталость (autonomic), оркестратор, модули под нагрузкой",
        [
            "AUTONOMIC_FATIGUE_ENABLED",
            "FATIGUE_P95_TELEGRAM_MS",
            "FATIGUE_P95_OPENROUTER_MS",
            "PREDICTIVE_BEHAVIOR_ENABLED",
            "PREDICTIVE_CONFIDENCE_THRESHOLD",
            "GOAL_ENGINE_ENABLED",
            "SELF_MAINTENANCE_ENABLED",
            "SELF_MAINTENANCE_INTERVAL_SEC",
            "SELF_IMPROVEMENT_ADVISOR_ENABLED",
            "HEAVY_MODULES_UNDER_PRESSURE",
            "PLUGIN_HOT_PROBE_AFTER_INSTALL",
        ],
    ),
    (
        "18. Резильенс и safe mode",
        [
            "RESILIENCE_AUTONOMY_ENABLED",
            "RESILIENCE_SAFE_ERROR_TOTAL",
            "RESILIENCE_CRITICAL_ERROR_TOTAL",
            "RESILIENCE_CRITICAL_FAILED_MODULES",
            "RESILIENCE_RECOVERY_OK_CYCLES",
            "RESILIENCE_ERROR_SAMPLE",
            "RESILIENCE_ERROR_COUNT_SEVERITIES",
            "SAFE_MODE_MODULE_ALLOWLIST",
        ],
    ),
    (
        "19. Автономные бэкапы",
        [
            "AUTONOMY_LAYER_ENABLED",
            "AUTONOMY_BACKUP_ROOT",
            "AUTONOMY_BACKUP_RETENTION",
            "AUTONOMY_BACKUP_EVERY_N_MAINTENANCE",
            "AUTONOMY_CRITICAL_PATHS",
        ],
    ),
    (
        "20. Метрики хоста (psutil)",
        [
            "RESOURCE_METRICS_TTL_SEC",
            "RESOURCE_WARN_CPU_PERCENT",
            "RESOURCE_WARN_MEM_PERCENT",
            "RESOURCE_WARN_DISK_PERCENT",
            "RESOURCE_CRIT_CPU_PERCENT",
            "RESOURCE_CRIT_MEM_PERCENT",
            "RESOURCE_CRIT_DISK_PERCENT",
            "RESOURCE_PRESSURE_DEGRADES",
            "RESOURCE_PRESSURE_CRITICAL",
        ],
    ),
    (
        "21. Паспорт развития",
        [
            "DEVELOPMENT_PASSPORT_PATH",
            "PASSPORT_BACKUP_DIR",
        ],
    ),
    (
        "22. Голос STT/TTS",
        [
            "VOICE_ENABLED",
            "VOICE_STT_ENABLED",
            "VOICE_STT_BACKEND",
            "VOICE_STT_MODEL_PATH",
            "VOICE_VOSK_FFMPEG",
            "VOICE_FFMPEG_BIN",
            "VOICE_STT_LOCAL_ONLY",
            "VOICE_TTS_ENABLED",
            "VOICE_TTS_BACKEND",
            "VOICE_TTS_MODEL_PATH",
            "VOICE_REPLY_ENABLED",
        ],
    ),
    (
        "23. Генерация изображений (/imagine): настройки кроме URL и моделей",
        [
            "IMAGE_GEN_ENABLED",
            "IMAGE_GEN_SIZE",
            "IMAGE_GEN_QUALITY",
            "IMAGE_GEN_TIMEOUT_SEC",
            "IMAGE_GEN_OUTPUT_DIR",
            "IMAGE_GEN_DAILY_LIMIT_PER_USER",
            "IMAGE_GEN_QUOTA_PATH",
        ],
    ),
    (
        "24. Тяжёлый воркер (PDF и т.д.)",
        [
            "HEAVY_WORKER_ENABLED",
            "HEAVY_WORKER_CONCURRENCY",
            "HEAVY_WORKER_QUEUE_MAX",
            "HEAVY_WORKER_TIMEOUT_SEC",
        ],
    ),
    (
        "25. Сеть, проверки доступности",
        [
            "CONNECTIVITY_CHECK_TIMEOUT_SEC",
            "CONNECTIVITY_INCLUDE_HTTP_PROBES",
            "NETWORK_PROBE_TIMEOUT_SEC",
            "GEMMA_LIVE_NETWORK",
        ],
    ),
    (
        "26. Память диалога и ретеншн",
        [
            "DIALOGUE_MEMORY_MAX",
            "DIALOGUE_MESSAGE_ARCHIVE_ENABLED",
            "DIALOGUE_MESSAGE_ARCHIVE_MAX",
            "RETENTION_LOG_DAYS",
            "RETENTION_BEHAVIOR_DAYS",
        ],
    ),
    (
        "27. UrlFetch",
        [
            "URL_FETCH_ENABLED",
            "URL_FETCH_BROWSER_COMPAT",
            "URL_FETCH_MAX_BYTES",
            "URL_FETCH_MAX_CHARS_RESPONSE",
            "URL_FETCH_MAX_REDIRECTS",
            "URL_FETCH_TIMEOUT_SEC",
            "URL_FETCH_MAX_CODE_CHARS",
        ],
    ),
    (
        "28. Site recipe",
        [
            "SITE_RECIPE_ENABLED",
            "SITE_RECIPE_AUTO_LEARN_ON_MISS",
            "SITE_RECIPE_AUTO_LEARN_WITH_LLM",
            "SITE_RECIPE_LEARN_ENABLED",
            "SITE_RECIPE_DIR",
            "SITE_RECIPE_USE_LLM",
            "SITE_RECIPE_LLM_HTML_CHARS",
            "SITE_RECIPE_CACHE_ENABLED",
            "SITE_RECIPE_CACHE_TTL_SEC",
            "SITE_RECIPE_EXTRACT_CODE",
            "SITE_RECIPE_MAX_CODE_TOTAL_CHARS",
        ],
    ),
    (
        "29. Время и отчёты",
        [
            "TZ",
            "GEMMA_REPORT_TIMEZONE",
        ],
    ),
    (
        "30. Math (intent_heuristics)",
        [
            "MATH_IMPLICIT_RAW_MAX_CHARS",
            "MATH_IMPLICIT_SCRUB_MAX_CHARS",
            "MATH_IMPLICIT_MAX_LETTERS",
            "MATH_AMBIGUOUS_CLARIFY",
        ],
    ),
    (
        "31. Прочие флаги поведения",
        [
            "STRATEGIC_LENSES_HINT_ENABLED",
            "LAW_SEARCH_ETAL_SEARCH",
        ],
    ),
]


def parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = line  # целая строка как в файле
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / ".env"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else root / ".env.out"

    raw = src.read_text(encoding="utf-8")
    kv = parse_env_lines(raw)

    ordered_keys: list[str] = []
    for _, keys in SECTIONS:
        for k in keys:
            if k in kv and k not in ordered_keys:
                ordered_keys.append(k)

    used = set(ordered_keys)
    rest = sorted(k for k in kv.keys() if k not in used)

    lines_out: list[str] = [
        "# =============================================================================",
        "# Конфигурация Gemma Agent (.env)",
        "# Структура: (1) ключи → (2) URL/хосты → (3) модели → (4+) настройки по блокам.",
        "# Секреты не коммитить. Сгенерировано/приведено к виду: scripts/format_env_layout.py",
        "# =============================================================================",
        "",
    ]

    for title, keys in SECTIONS:
        block_lines = [kv[k] for k in keys if k in kv]
        if not block_lines:
            continue
        lines_out.append(f"# =============================================================================")
        lines_out.append(f"# {title}")
        lines_out.append(f"# =============================================================================")
        lines_out.extend(block_lines)
        lines_out.append("")

    if rest:
        lines_out.append("# =============================================================================")
        lines_out.append("# 32. Остальные переменные (добавьте в scripts/format_env_layout.py при желании)")
        lines_out.append("# =============================================================================")
        for k in rest:
            lines_out.append(kv[k])
        lines_out.append("")

    lines_out.append(
        "# =============================================================================\n"
        "# Конец. Утечка TELEGRAM_TOKEN → BotFather /revoke.\n"
        "# ============================================================================="
    )

    dst.write_text("\n".join(lines_out).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {dst} ({len(kv)} vars, {len(rest)} in overflow section)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
