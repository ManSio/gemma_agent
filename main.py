"""
Главная точка запуска универсального социального ассистента
"""
import logging
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from core.autopilot_mode import apply_autopilot_defaults, autopilot_enabled

_autopilot_applied = apply_autopilot_defaults()

from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine
from core.orchestrator import Orchestrator
from core.input_layer import InputLayer
from core.openrouter_provider import get_openrouter_provider
from core.mem0_memory.mem0_module import Mem0MemoryModule, load_mem0_config_from_env
from core.brain import configure_brain_memory
from core.self_learning.lesson_manager import LessonManager
from core.self_healing import SelfHealingEngine
from core.self_programming import SelfProgrammingModule, set_plugin_registry_for_tools
from core.runtime_diagnostic_module import set_orchestrator_for_runtime_diagnostic
from core.behavior_store import BehaviorStore
from core.group_behavior import GroupBehaviorModule
from core.user_system import UserSystemModule
from core.psychology_engine import PsychologyEngineModule
from core.digital_twin import DigitalTwinModule
from core.persona_engine import PersonaEngineModule
from core.config_manager import get_config
from core.runtime_layout import ensure_runtime_data_layout
from core.boot_timeline import mark_boot
from core.logging_setup import setup_logging

# После всех импортов: иначе сторонние модули могут повесить лишние handlers на root
# (в логах одна строка: несколько «INFO» подряд + JSON / обрезанный вывод).
setup_logging()

logger = logging.getLogger(__name__)
if autopilot_enabled():
    logger.info(
        "Autopilot mode enabled; defaults applied: %s",
        sorted(_autopilot_applied.keys()),
        extra={"gemma_event": "autopilot_enabled", "applied_keys": sorted(_autopilot_applied.keys())},
    )

ensure_runtime_data_layout()
mark_boot("after_runtime_layout")


async def ensure_runtime_files() -> None:
    """Проверить и создать отсутствующие файлы runtime и коллекции Qdrant при старте."""
    # Проверка и создание JSONL-файлов
    runtime_files = [
        "data/runtime/self_learning_lessons.jsonl",
        "data/runtime/performance_log.jsonl",
        "data/runtime/reflexion_lessons.jsonl",
        "data/runtime/autotune_state.json",
        "data/runtime/kv_session_state.json",
        "data/runtime/error_memory.jsonl",
        "data/runtime/guardian_actions.jsonl",
    ]
    from pathlib import Path
    for file in runtime_files:
        path = Path(file)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            logger.info("[guardian] created missing file: %s", file)

    # Qdrant: env обязателен; при QDRANT_STARTUP_STRICT=true (default) — fail-fast без связи
    from core.qdrant_startup import ensure_qdrant_at_startup

    ensure_qdrant_at_startup()


async def main():
    mark_boot("async_main_start")
    logger.info(
        "═════════ gemma_bot boot ═════════",
        extra={"gemma_event": "boot_start"},
    )
    logger.info("Starting Universal Social Assistant...", extra={"gemma_event": "boot"})
    cfg_check = get_config().validate()
    if not cfg_check.get("ok"):
        raise ValueError(f"Invalid configuration: {cfg_check.get('errors')}")
    if cfg_check.get("warnings"):
        logger.warning("Configuration warnings: %s", cfg_check.get("warnings"))

    # ── Запустить фоновый воркер event-bus (fire-and-forget очередь) ──
    from core.event_bus import bus as _event_bus
    _event_bus.start_ff_worker()
    logger.info("[event_bus] ff-worker started")

    # ============================================================
    #   1. Plugin Registry
    # ============================================================
    mark_boot("before_plugin_registry")
    modules_path = os.getenv("MODULES_PATH", "./modules")
    plugin_registry = PluginRegistry(modules_path)
    from core.plugin_registry import set_plugin_registry

    set_plugin_registry(plugin_registry)

    # ============================================================
    #   2. Policy Engine
    # ============================================================
    policy_engine = PolicyEngine()

    # ============================================================
    #   3. OpenRouter Provider
    # ============================================================
    openrouter = get_openrouter_provider()

    # ============================================================
    #   4. Mem0 Memory
    # ============================================================
    mem0_memory = Mem0MemoryModule(load_mem0_config_from_env())
    configure_brain_memory(mem0_memory)
    from core.connectivity_check import log_mem0_startup_status

    await log_mem0_startup_status(mem0_memory, logger)

    # ── Проверка и создание runtime-файлов + Qdrant коллекций ──
    await ensure_runtime_files()

    # ── Загрузка уроков self-learning в память ──
    try:
        mgr = LessonManager.get_instance()
        lessons = mgr.load_lessons()
        logger.info("[self_learning] loaded %d lessons at startup", len(lessons))
    except Exception:
        logger.warning("[self_learning] failed to load lessons at startup", exc_info=True)

    behavior_store = BehaviorStore()
    group_behavior = GroupBehaviorModule()
    user_system = UserSystemModule()
    psychology_engine = PsychologyEngineModule()
    digital_twin = DigitalTwinModule()
    persona_engine = PersonaEngineModule()
    self_programming = SelfProgrammingModule(modules_path=modules_path)

    # ============================================================
    #   5. Orchestrator
    # ============================================================
    orchestrator = Orchestrator(
        plugin_registry=plugin_registry,
        policy_engine=policy_engine,
        openrouter=openrouter,
        mem0_memory=mem0_memory,
        group_behavior=group_behavior,
        behavior_store=behavior_store,
        user_system=user_system,
        psychology_engine=psychology_engine,
        digital_twin=digital_twin,
        persona_engine=persona_engine,
        self_programming=self_programming,
    )
    set_orchestrator_for_runtime_diagnostic(orchestrator)

    # ============================================================
    #   6. Telegram Input Layer
    # ============================================================
    bot_token = os.getenv("TELEGRAM_TOKEN")
    if not bot_token:
        raise ValueError("TELEGRAM_TOKEN is required")

    mark_boot("before_input_layer")
    input_layer = InputLayer(
        bot_token=bot_token,
        plugin_registry=plugin_registry,
        orchestrator=orchestrator,
        policy_engine=policy_engine,
        openrouter=openrouter,
        mem0_memory=mem0_memory,
    )

    # ============================================================
    #   7. Load all modules
    # ============================================================
    logger.info("Loading plugins (manifest → enable)…", extra={"gemma_event": "plugins_load_start"})
    mark_boot("before_load_all_modules")
    plugin_registry.load_all_modules()
    set_plugin_registry_for_tools(plugin_registry)
    mark_boot("plugins_ready", plugin_count=len(plugin_registry.loaded_modules))
    logger.info(
        "Plugins ready: %s enabled",
        len(plugin_registry.loaded_modules),
        extra={"gemma_event": "plugins_load_done", "plugin_count": len(plugin_registry.loaded_modules)},
    )
    try:
        orchestrator._recovery_autonomy.post_boot(orchestrator)
    except Exception as e:
        logger.debug("recovery_autonomy post_boot: %s", e)
    try:
        orchestrator._resilience.post_boot_recovery(orchestrator)
    except Exception as e:
        logger.warning("post_boot_recovery failed: %s", e, exc_info=True)
    mark_boot("after_post_boot_hooks")

    # ============================================================
    #   8. Self-Healing Engine
    # ============================================================
    self_healing = SelfHealingEngine()
    asyncio.create_task(self_healing.start_monitoring(plugin_registry))

    # ============================================================
    #   9. Start bot
    # ============================================================
    from core.telegram_webhook_config import resolve_telegram_webhook_url

    _raw_webhook = os.getenv("WEBHOOK_URL", "").strip()
    webhook_url = resolve_telegram_webhook_url(_raw_webhook)
    if _raw_webhook and not webhook_url:
        logger.warning(
            "WEBHOOK_URL=%r looks like .env placeholder — using Telegram polling instead",
            _raw_webhook[:80],
            extra={"gemma_event": "telegram_webhook_placeholder_fallback"},
        )
    if webhook_url:
        webhook_path = os.getenv("WEBHOOK_PATH", "/webhook").strip()
        webhook_host = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
        webhook_port = int(os.getenv("WEBHOOK_PORT", "8443"))
        webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
        logger.info(
            "Starting Telegram webhook on %s:%s...", webhook_host, webhook_port,
            extra={"gemma_event": "telegram_webhook_start"},
        )
        mark_boot("before_start_webhook")
        await input_layer.start_webhook(
            webhook_url=webhook_url,
            webhook_path=webhook_path,
            host=webhook_host,
            port=webhook_port,
            secret_token=webhook_secret,
        )
    else:
        logger.info("Starting Telegram polling…", extra={"gemma_event": "telegram_poll_start"})
        mark_boot("before_await_start_polling")
        await input_layer.start_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
