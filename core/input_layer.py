"""
Input Layer — входной слой Telegram, финальная версия
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import Message, ReplyParameters

from core.models import Input, Output
from core.plugin_registry import PluginRegistry
from core.orchestrator import Orchestrator
from core.policy_engine import PolicyEngine
from core.openrouter_provider import OpenRouterProvider
from core.mem0_memory.mem0_module import Mem0MemoryModule
from core.async_spawn import spawn_logged
from core.event_bus import bus
from core.error_analysis import record_error_event
from core.link_safety import LinkSafetyModule
from core.admin_module import AdminModule
from core.autonomy_module import AutonomyModule
from core.voice_module import VoiceModule
from core.greetings_module import GreetingsModule
from core.user_management_module import UserManagementModule
from core.file_intake import FileContext, FileIntakeModule
from core.document_intake import DocumentIntakeModule
from core.code_intake import CodeIntakeLayer
from core.security_manager import SecurityManager
from core.monitoring import MONITOR
from core.response_adapter import UnifiedResponseAdapter
from core.number_parse import parse_env_float, parse_loose_float
from core.observability import OBS
from core.task_worker import WORKER
from core.input_handlers import register_all_handlers
from core.input_handlers.admin_access import effective_user_scope
from core.input_handlers.admin_slash_dispatch import try_dispatch_admin_slash
from core.input_handlers.inline_slash_dispatch import dispatch_core_slash_runner, try_dispatch_inline_slash
from core.input_handlers.slash_exclusive import orchestrator_should_skip_slash
from core.manifest_buttons import merge_manifest_buttons_keyboards
from core.telegram_inline_meta import inline_markup_from_meta
from core.boot_timeline import mark_boot
from core.env_flags import env_truthy, gemma_core_log_full
from core.prompt_routing import brain_fast_chitchat_eligible, private_dm_chitchat_continuity_override
from core.group_chat_policy import load_group_chat_policy

_GROUP_QUESTION_STARTERS = (
    "кто ",
    "что ",
    "как ",
    "где ",
    "когда ",
    "зачем ",
    "почему ",
    "сколько ",
    "какой ",
    "какая ",
    "какие ",
    "можно ли ",
    "а что ",
    "а как ",
    "ну как ",
)


def _group_open_question(text: str) -> bool:
    """Открытый вопрос в группе (balanced mode), без @бота."""
    t = (text or "").strip()
    if not t or t.startswith("/") or len(t) > 500:
        return False
    if "?" in t or "？" in t:
        return True
    low = t.lower()
    return any(low.startswith(s) for s in _GROUP_QUESTION_STARTERS)
from core.telegram_progress import (
    telegram_progress_arm,
    telegram_progress_disarm,
    telegram_progress_pulse,
    telegram_progress_seed_from_user_text,
    telegram_progress_start_etc_refresh,
)
from core.telegram_stream_reply import (
    clear_chat_cancel,
    register_chat_cancel,
    telegram_stream_bind_progress,
    telegram_stream_disarm,
    telegram_stream_has_delivery,
    telegram_stream_should_bind,
    telegram_stream_take_delivery,
)
from core.telegram_stream_reasoning import (
    arm_admin_stream_reasoning,
    disarm_admin_stream_reasoning,
)
from core.telegram_util import reply_text_chunks, sanitize_html
from core.user_image_pending import has_pending_image, pop_pending_images, register_pending_image
from core.pending_flow import try_handle_negative_interrupt
from core.pending_flow_bootstrap import install as _install_pending_flow

_install_pending_flow()

logger = logging.getLogger(__name__)


def _looks_like_long_prose_discussion(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 260:
        return False
    low = t.lower()
    # Явная операционная/системная речь — не подавляем служебные подсказки.
    if any(k in low for k in ("/admin_", "/calc", "команда", "debug", "диагност", "настрой")):
        return False
    sentence_like = sum(t.count(ch) for ch in ".!?")
    newline_like = t.count("\n")
    # Много естественного текста (абзацы/предложения) и мало признаков командной формы.
    return (sentence_like + newline_like) >= 3


def _is_intrusive_service_reply(out: Output) -> bool:
    if out.type != "text":
        return False
    meta = out.meta or {}
    txt = str(out.payload or "").strip()
    low = txt.lower()
    if not txt:
        return False
    if bool(meta.get("confirmation")):
        return True
    if str(meta.get("reason") or "") == "math_ambiguous":
        return True
    if (
        "для расчёта выражения отправьте команду вида: /calc" in low
        or "обычный текст обрабатывается диалогом" in low
    ):
        return True
    if txt.startswith("Запомнить ") and "Ответь «да» или «нет»." in txt:
        return True
    if txt.startswith("Похоже, ты обновляешь: ") and "Ответь «да» или «нет»." in txt:
        return True
    if "уточни, пожалуйста, базовую валюту" in low:
        # Не выкидывать целый длинный ответ, если вопрос про валюту только часть текста.
        if len(txt) > 420:
            return False
        return True
    if "ничего не хочешь уточнить" in low or "ты пока ничего не хочешь" in low:
        return True
    if "не вижу время для напоминания" in low or "не вижу времени для напоминания" in low:
        return True
    return False


def _outputs_only_standalone_currency_nags(outputs: List[Output]) -> bool:
    """Короткие шаблонные напоминания про базовую валюту (без основного ответа)."""
    if not outputs:
        return False
    for o in outputs:
        if o.type != "text":
            return False
        meta = o.meta or {}
        if bool(meta.get("confirmation")) or str(meta.get("reason") or "") == "math_ambiguous":
            return False
        txt = str(o.payload or "").strip()
        low = txt.lower()
        if "уточни, пожалуйста, базовую валюту" not in low:
            return False
        if len(txt) > 560:
            return False
    return True


def _has_substantive_assistant_reply(outputs: List[Output]) -> bool:
    """Уже есть содержательный ответ — не дублировать сервисными «Запомнить…»."""
    for o in outputs:
        if o.type != "text":
            continue
        meta = o.meta or {}
        if bool(meta.get("confirmation")) or str(meta.get("reason") or "") == "math_ambiguous":
            continue
        if len(str(o.payload or "").strip()) >= 80:
            return True
    return False


def _apply_anti_intrusion_guard(
    user_text: str, outputs: List[Output]
) -> Tuple[List[Output], bool]:
    """Возвращает (outputs, silent_skip): при silent_skip не слать ответ пользователю."""
    if not outputs:
        return outputs, False
    if not _looks_like_long_prose_discussion(user_text) and not _has_substantive_assistant_reply(
        outputs
    ):
        return outputs, False
    kept = [o for o in outputs if not _is_intrusive_service_reply(o)]
    if len(kept) == len(outputs):
        return outputs, False
    MONITOR.inc("anti_intrusion_guard_trigger_total")
    if kept:
        return kept, False
    if _outputs_only_standalone_currency_nags(outputs):
        try:
            MONITOR.inc("anti_intrusion_guard_silent_skip_total")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        return [], True
    return outputs, False


def _reply_suspect_incomplete(text: str) -> bool:
    """Эвристика: длинный ответ без завершения предложения — возможен обрыв генерации/чанка."""
    t = (text or "").strip()
    if len(t) < 60:
        return False
    last = t[-1]
    if last in ".!?…。！？」»\"')":
        return False
    if t.endswith("...") or t.endswith("…"):
        return False
    return len(t) >= 120 and last.isalnum()


def _log_telegram_turn_summary(
    *,
    trace_id: str,
    user_id: str,
    chat_id: str,
    user_text: str,
    plan: Any,
    outputs: List[Output],
) -> None:
    if env_truthy("GEMMA_LOG_TURN_SUMMARY_OFF", False):
        return
    # По умолчанию — только WARNING при подозрении на обрыв; каждый ход в INFO: GEMMA_CORE_LOG_FULL или GEMMA_LOG_TURN_VERBOSE=1
    verbose = gemma_core_log_full() or env_truthy("GEMMA_LOG_TURN_VERBOSE", False)
    mods = [s.module_name for s in plan.steps] if plan.steps else []
    mod0 = mods[0] if mods else ""
    tid = (trace_id or "")[:12]
    for i, out in enumerate(outputs):
        if out.type != "text":
            continue
        body = str(out.payload or "").strip()
        if not body:
            continue
        susp = _reply_suspect_incomplete(body)
        msg = (
            "[turn] trace=%s user_id=%s chat_id=%s module=%s part=%s user_chars=%s reply_chars=%s "
            "suspect_incomplete=%s"
        )
        args = (tid, user_id, chat_id, mod0, i, len(user_text), len(body), susp)
        if susp:
            try:
                MONITOR.inc("telegram_reply_suspect_incomplete_total")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            logger.warning(
                msg,
                *args,
                extra={
                    "gemma_event": "telegram_turn",
                    "trace_id": trace_id,
                    "suspect_incomplete": True,
                    "reply_chars": len(body),
                },
            )
        elif verbose:
            logger.info(
                msg,
                *args,
                extra={
                    "gemma_event": "telegram_turn",
                    "trace_id": trace_id,
                    "suspect_incomplete": False,
                    "reply_chars": len(body),
                },
            )

# Лимит подписи к медиа/файлу в Telegram (символы)
_TELEGRAM_CAPTION_MAX = 1024


def telegram_forward_preamble(message: Message) -> str:
    """Одна строка про пересланное сообщение (если есть), чтобы модель не теряла контекст."""
    fo = getattr(message, "forward_origin", None)
    if fo is None:
        return ""
    try:
        tname = type(fo).__name__
        if "User" in tname and hasattr(fo, "sender_user"):
            su = fo.sender_user
            if su is not None:
                un = (getattr(su, "username", None) or "").strip()
                if un:
                    return f"Сообщение переслано от @{un}."
                return f"Сообщение переслано от пользователя (id {getattr(su, 'id', '')})."
        if "HiddenUser" in tname:
            sn = (getattr(fo, "sender_user_name", None) or "").strip()
            return f"Сообщение переслано (скрытый отправитель: {sn or 'не указан'})."
        if "Chat" in tname:
            ch = getattr(fo, "sender_chat", None)
            title = (getattr(ch, "title", None) or "").strip() if ch is not None else ""
            if title:
                return f"Сообщение переслано из чата «{title}»."
    except Exception as e:
        logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
    return "Сообщение переслано."


def telegram_reply_context_from_message(message: Message, *, bot_user_id: Optional[int] = None) -> str:
    """
    Текст цепочки reply_to_message (от ближайшего предка к более ранним), чтобы короткая реплика
    в ветке не выглядела для модели как «новый диалог».
    """
    if not message.reply_to_message:
        return ""
    try:
        max_depth = max(1, min(6, int((os.getenv("TELEGRAM_REPLY_CONTEXT_DEPTH") or "3").strip())))
    except ValueError:
        max_depth = 3
    try:
        max_chars = max(400, min(20000, int((os.getenv("TELEGRAM_REPLY_CONTEXT_MAX_CHARS") or "4500").strip())))
    except ValueError:
        max_chars = 4500
    chunks: List[str] = []
    cur: Optional[Message] = message.reply_to_message
    depth = 0
    while cur is not None and depth < max_depth:
        depth += 1
        who = "участник чата"
        try:
            fu = cur.from_user
            if fu and bot_user_id is not None and fu.id == bot_user_id:
                who = "бот"
            elif fu and (fu.username or "").strip():
                who = f"@{fu.username.strip()}"
            elif fu:
                who = f"user_id={fu.id}"
        except Exception as e:
            logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        body = (cur.text or cur.caption or "").strip()
        if not body:
            if cur.photo:
                body = "[фото без подписи]"
            elif cur.document:
                fn = (getattr(cur.document, "file_name", None) or "").strip()
                body = f"[документ: {fn or 'файл'}]"
            elif getattr(cur, "sticker", None):
                body = "[стикер]"
            else:
                body = "[нет текста]"
        chunks.append(f"↑ уровень {depth} ({who}):\n{body}")
        cur = cur.reply_to_message
    if not chunks:
        return ""
    out = "\n\n———\n\n".join(chunks)
    if len(out) > max_chars:
        out = out[: max(0, max_chars - 24)] + "\n… (цепочка reply обрезана)"
    return out


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _telegram_progress_initial_text() -> str:
    t = (os.getenv("TELEGRAM_PROGRESS_INITIAL") or "").strip()
    return t if t else "⏳ Думаю…"


def _reply_to_user_kwargs(message: Message) -> Dict[str, Any]:
    """Ответ цепляется к сообщению пользователя (как «ответ» в Telegram). TELEGRAM_REPLY_TO_USER_MESSAGE=false — выкл."""
    if not _env_truthy("TELEGRAM_REPLY_TO_USER_MESSAGE", True):
        return {}
    try:
        mid = int(message.message_id)
    except (TypeError, ValueError):
        return {}
    return {"reply_parameters": ReplyParameters(message_id=mid)}


def _telegram_progress_eligible(message: Message, plan: Any) -> bool:
    if not _env_truthy("TELEGRAM_PROGRESS_UI", True):
        return False
    if message.chat.type != ChatType.PRIVATE:
        if not _env_truthy("TELEGRAM_PROGRESS_IN_GROUPS", False):
            return False
    steps = getattr(plan, "steps", None) or []
    if not steps:
        return False
    mk = getattr(steps[0], "module_name", "") or ""
    return mk in {"chat-orchestrator", "chat_orchestrator", "smartchat"}


class InputLayer:
    """Входной слой Telegram — минимальный, стабильный, без лишних зависимостей"""

    def __init__(
        self,
        bot_token: str,
        plugin_registry: PluginRegistry,
        orchestrator: Orchestrator,
        policy_engine: PolicyEngine,
        openrouter: Optional[OpenRouterProvider] = None,
        mem0_memory: Optional[Mem0MemoryModule] = None,
    ):
        # Дефолт выше типичного aiohttp-таймаута: медленный Docker/VPN до api.telegram.org
        _http_timeout = parse_env_float("TELEGRAM_HTTP_TIMEOUT", 240.0)
        _proxy = (os.getenv("TELEGRAM_PROXY") or os.getenv("HTTPS_PROXY") or "").strip() or None
        self.bot = Bot(
            token=bot_token,
            session=AiohttpSession(proxy=_proxy, timeout=_http_timeout),
        )
        from core.user_bug_report import register_bot

        register_bot(self.bot)
        try:
            from core.admin_ops_notify import register_admin_ops_bot

            register_admin_ops_bot(self.bot)
        except Exception as e:
            logger.debug("admin_ops_notify register: %s", e)
        # Установка healers (event-driven self-healing)
        try:
            from core.event_healers import install_healers

            install_healers()
        except Exception as e:
            logger.debug("healers install: %s", e)
        # Установка LLM Triage
        try:
            from core.llm_triage import install_triage

            install_triage()
        except Exception as e:
            logger.debug("llm_triage install: %s", e)
        # Установка Self-Improvement (health checks, metric_ts, profile_reinforcement)
        try:
            from core.self_improvement import install_self_improvement

            install_self_improvement()
        except Exception as e:
            logger.debug("self_improvement install: %s", e)
        try:
            from core.turn_observer import install_turn_observer
            from core.ops_trace import install_ops_trace

            install_turn_observer()
            install_ops_trace()
            from core.turn_quality_loop import install_turn_quality_loop

            install_turn_quality_loop()
        except Exception as e:
            logger.debug("turn_observer install: %s", e)
        self.dp = Dispatcher()

        self.plugin_registry = plugin_registry
        self.orchestrator = orchestrator
        self.policy_engine = policy_engine
        self.openrouter = openrouter
        self.mem0_memory = mem0_memory

        self._bot_user_id: Optional[int] = None
        self._bot_username: Optional[str] = None
        self._identity_lock = asyncio.Lock()
        self._pipeline_chat_locks: Dict[str, asyncio.Lock] = {}
        self._pipeline_chat_locks_guard = asyncio.Lock()
        self._bot_identity_gave_up = False
        self._seen_private_users = self._load_seen_private_intro_users()
        self._seen_private_users_lock = asyncio.Lock()
        self._link_safety = LinkSafetyModule()
        self._admin_module = AdminModule(orchestrator=self.orchestrator, behavior_store=getattr(self.orchestrator, "behavior_store", None))
        self._autonomy = AutonomyModule()
        self._voice = VoiceModule()
        self._greetings = GreetingsModule()
        self._user_mgmt = UserManagementModule(
            behavior_store=getattr(self.orchestrator, "behavior_store", None),
            user_facts_manager=getattr(self.orchestrator, "user_facts_manager", None),
            user_system=getattr(self.orchestrator, "user_system", None),
            digital_twin=getattr(self.orchestrator, "digital_twin", None),
        )
        self._file_intake = FileIntakeModule()
        self._document_intake = DocumentIntakeModule()
        self._code_intake = CodeIntakeLayer()
        self._security = SecurityManager()
        self._response_adapter = UnifiedResponseAdapter()

        self._setup_handlers()
        self._setup_events()

    # ============================================================
    #   HANDLERS
    # ============================================================
    async def _on_dispatcher_startup(self) -> None:
        """get_me + меню команд — до конца startup (polling ждёт); остальное — в фоне."""
        try:
            from core.startup_notify import send_startup_dm_to_admins
            from core.telegram_bot_menu import sync_telegram_bot_menu

            await self._ensure_bot_identity()
            try:
                await sync_telegram_bot_menu(self.bot, self.plugin_registry)
            except Exception as e:
                logger.warning("telegram bot menu: %s", e)

            async def _bootstrap_rest() -> None:
                try:
                    from core.reminder_dispatch import boot_tick_reminders, register_reminder_bot

                    register_reminder_bot(self.bot)
                    await boot_tick_reminders(self.bot)
                except Exception as e:
                    logger.warning("reminder boot tick: %s", e)
                try:
                    await send_startup_dm_to_admins(self.bot, self.orchestrator)
                except Exception as e:
                    logger.warning("startup notify: %s", e)
                try:
                    from core.diagnostic_bundle import schedule_boot_diagnostic_if_configured

                    spawn_logged(
                        schedule_boot_diagnostic_if_configured(self.bot, self.orchestrator, self._admin_module),
                        label="boot_diagnostic",
                    )
                except Exception as e:
                    logger.warning("boot diagnostic schedule: %s", e)
                try:
                    from core.usage_learning import ensure_loaded as _usage_ensure_loaded

                    _usage_ensure_loaded()
                except Exception as e:
                    logger.warning("usage_learning preload: %s", e)
                try:
                    from core.autopilot_cycle import start_autopilot_cycle

                    spawn_logged(
                        start_autopilot_cycle(self.orchestrator, bot=self.bot),
                        label="autopilot_cycle",
                    )
                except Exception as e:
                    logger.warning("autopilot cycle start: %s", e)
                try:
                    from core.autotune import start_autotune_loop

                    start_autotune_loop()
                except Exception as e:
                    logger.warning("autotune loop start: %s", e)
                try:
                    from core.reflexion import start_reflexion_loop

                    start_reflexion_loop()
                except Exception as e:
                    logger.warning("reflexion loop start: %s", e)
                try:
                    from core.self_learning import LessonManager

                    _self_learning_mgr = LessonManager.get_instance()
                    _self_learning_mgr.apply_forgetting_curve()
                except Exception as e:
                    logger.warning("self_learning bootstrap: %s", e)

            spawn_logged(_bootstrap_rest(), label="input_layer_bootstrap")
        except Exception as e:
            logger.warning("startup bootstrap: %s", e)

    def _setup_handlers(self):
        register_all_handlers(self)
        self.dp.startup.register(self._on_dispatcher_startup)

    # ============================================================
    #   EVENTS
    # ============================================================
    def _setup_events(self):
        from core.nervous_system import install_nervous_system

        bus.subscribe("module.enabled", lambda d: logger.info(f"Module enabled: {d.get('module')}"))
        bus.subscribe("module.disabled", lambda d: logger.info(f"Module disabled: {d.get('module')}"))
        bus.subscribe("module.failed", lambda d: logger.error(f"Module failed: {d.get('module')}"))
        bus.subscribe("reflex.applied", lambda d: logger.warning(f"Reflex applied: {d}"))
        install_nervous_system()

    # ============================================================
    #   GROUP FILTER (mention / commands / reply to bot)
    # ============================================================
    async def _ensure_bot_identity(self) -> None:
        """Один быстрый get_me для фильтра в группах; без «подвисания» на TELEGRAM_HTTP_TIMEOUT на каждое сообщение."""
        if self._bot_user_id is not None:
            return
        if self._bot_identity_gave_up:
            return
        async with self._identity_lock:
            if self._bot_user_id is not None or self._bot_identity_gave_up:
                return
            timeout = parse_env_float("TELEGRAM_GET_ME_TIMEOUT_SEC", 25.0)
            try:
                me = await asyncio.wait_for(self.bot.get_me(), timeout=timeout)
                self._bot_user_id = me.id
                self._bot_username = (me.username or "").lower()
            except Exception as e:
                logger.warning(
                    "get_me не удался (в группах работают /команды; @бот — после восстановления сети или TELEGRAM_PROXY): %s",
                    e,
                )
                self._bot_identity_gave_up = True

    def _is_group_chat(self, message: Message) -> bool:
        return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    def _should_process_group_message(self, message: Message) -> bool:
        return self._group_trigger_kind(message) is not None

    def _group_trigger_kind(self, message: Message) -> Optional[str]:
        text = (message.text or message.caption or "").strip()
        if bool(getattr(message.from_user, "is_bot", False)):
            return None
        loc0 = getattr(message, "location", None)
        if loc0 is not None:
            if message.reply_to_message and self._bot_user_id and message.reply_to_message.from_user:
                if message.reply_to_message.from_user.id == self._bot_user_id:
                    return "reply"
            try:
                policy = load_group_chat_policy()
            except Exception:
                policy = {}
            if not isinstance(policy, dict):
                policy = {}
            mode = str(policy.get("participate_mode") or ("active" if policy.get("active_mode") else "mention"))
            if mode == "active":
                return "active_mode"
            return None
        if text.startswith("/"):
            return "command"
        try:
            policy = load_group_chat_policy()
        except Exception:
            policy = {"active_mode": False, "participate_mode": "mention"}
        mode = str(policy.get("participate_mode") or ("active" if policy.get("active_mode") else "mention"))
        if mode == "active" and text:
            return "active_mode"
        if mode == "balanced" and text and _group_open_question(text):
            return "question"
        if self._bot_user_id and message.reply_to_message and message.reply_to_message.from_user:
            if message.reply_to_message.from_user.id == self._bot_user_id:
                return "reply"
        entities = message.entities or message.caption_entities
        if entities and self._bot_username:
            for ent in entities:
                if ent.type == MessageEntityType.MENTION:
                    part = text[ent.offset : ent.offset + ent.length].lstrip("@").lower()
                    if part == self._bot_username:
                        return "mention"
                if ent.type == MessageEntityType.TEXT_MENTION and ent.user and self._bot_user_id:
                    if ent.user.id == self._bot_user_id:
                        return "mention"
        loose_name = mode == "balanced" or _env_truthy("GROUP_LOOSE_BOTNAME_TRIGGER", False)
        if self._bot_username and loose_name:
            blob = text.lower()
            un = self._bot_username.lower()
            if un and re.search(r"(?<![a-z0-9_])" + re.escape(un) + r"(?![a-z0-9_])", blob):
                return "mention"
        return None

    async def _attach_group_chat_snapshot(self, message: Message, meta: Dict[str, Any]) -> None:
        if not self._is_group_chat(message):
            return
        try:
            c = await message.bot.get_chat(message.chat.id)
            snap: Dict[str, Any] = {}
            if getattr(c, "title", None):
                snap["title"] = c.title
            mc = getattr(c, "member_count", None)
            if mc is not None:
                snap["member_count"] = int(mc)
            if snap:
                meta["group_chat_snapshot"] = snap
        except Exception as e:
            logger.debug("group chat snapshot: %s", e)

    async def _notify_admins_new_access_request(self, user_id: str, username: Optional[str], full_name: str) -> None:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        ids: list[str] = []
        for key in ("ADMIN_NOTIFY_USER_IDS", "ADMIN_USER_IDS"):
            raw = (os.getenv(key) or "").strip()
            if raw:
                ids.extend(x.strip() for x in raw.split(",") if x.strip())
        seen: set[str] = set()
        uniq = []
        for x in ids:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        if not uniq:
            return
        un = f"@{username}" if username else "—"
        fn = full_name or "—"
        text = sanitize_html(
            "🔔 <b>Новая заявка в бот</b>\n"
            f"id: <code>{user_id}</code>\n"
            f"username: {un}\n"
            f"имя: {fn}\n\n"
            "<i>Полная панель: <code>/admin_access</code></i>"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Пустить", callback_data=f"acc:ok:{user_id}"),
                    InlineKeyboardButton(text="⛔ Отказ", callback_data=f"acc:no:{user_id}"),
                ]
            ]
        )
        for aid in uniq:
            try:
                await self.bot.send_message(int(aid), text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.debug("access notify %s: %s", aid, e)

    def _maybe_bump_guest_reply_quota(self, message: Message, user_id: str, delta: int) -> None:
        """Счётчик гостевых ответов (ЛС, заявка в очереди)."""
        if delta <= 0 or message.chat.type != "private":
            return
        try:
            from core import access_gate as _agate

            if not _agate.is_approval_required() or _agate.guest_reply_quota() <= 0:
                return
            if self._admin_module.is_admin(user_id):
                return
            dec = _agate.evaluate_private_user(user_id, is_admin=False)
            if dec not in ("pending", "enqueue"):
                return
            _agate.increment_guest_replies(user_id, delta)
        except Exception as e:
            logger.debug("guest reply bump: %s", e)

    # ============================================================
    #   PROCESS MESSAGE
    # ============================================================
    def _private_intro_enabled(self) -> bool:
        raw = (os.getenv("PRIVATE_INTRO_ON_FIRST_DM") or "true").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _seen_private_intro_path(self) -> Path:
        raw = (os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip()
        base = Path(raw) if Path(raw).is_absolute() else Path(__file__).resolve().parent.parent / raw
        return (base / "seen_private_intro_users.json").resolve()

    def _load_seen_private_intro_users(self) -> set:
        path = self._seen_private_intro_path()
        if not path.is_file():
            return set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return {str(x).strip() for x in data if str(x).strip()}
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("seen_private_intro load: %s", e)
        return set()

    def _persist_seen_private_intro_user(self, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid:
            return
        path = self._seen_private_intro_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            current = self._load_seen_private_intro_users()
            current.add(uid)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(current), f, ensure_ascii=False)
        except OSError as e:
            logger.debug("seen_private_intro save: %s", e)

    def _user_has_prior_private_dialogue(self, user_id: str) -> bool:
        store = getattr(self.orchestrator, "behavior_store", None)
        if not store or not user_id:
            return False
        try:
            rec = store.load(user_id, None)
            msgs = rec.get("recent_messages") if isinstance(rec, dict) else None
            return bool(msgs)
        except Exception as e:
            logger.debug("prior dialogue check: %s", e)
            return False

    def _should_send_private_intro(self, user_id: str, user_text: str) -> bool:
        if not self._private_intro_enabled():
            return False
        uid = str(user_id or "").strip()
        if not uid or uid in self._seen_private_users:
            return False
        if self._user_has_prior_private_dialogue(uid):
            self._seen_private_users.add(uid)
            self._persist_seen_private_intro_user(uid)
            return False
        t = (user_text or "").strip()
        if len(t) > 12 and not re.match(
            r"(?i)^\s*(привет|здравств|добрый|hi|hello|hey|спасибо|пока)\b",
            t,
        ):
            self._seen_private_users.add(uid)
            self._persist_seen_private_intro_user(uid)
            return False
        return True

    def note_private_user_seen_for_intro(self, user_id: str) -> None:
        """Пользователь уже получил приветствие вне pipeline (например /start) — не слать private_intro при первом тексте."""
        if user_id:
            self._seen_private_users.add(user_id)  # called synchronously from /start handler
            self._persist_seen_private_intro_user(user_id)

    def _message_has_actor(self, message: Message, actor_user_id: Optional[str] = None) -> bool:
        """Некоторые Telegram updates могут приходить без from_user; не роняем pipeline на них."""
        if (actor_user_id or "").strip():
            return True
        return message.from_user is not None

    def _resolve_error_hint(self, exc: BaseException) -> str:
        """Короткая операторская подсказка: как исправить типовую ошибку."""
        et = type(exc).__name__
        em = str(exc or "")
        low = em.lower()
        if "could not convert string to float" in low:
            return (
                "Проверьте .env: числовые переменные (таймауты, LATENCY_TRACE_SLOW_MS, "
                "CONNECTIVITY_CHECK_TIMEOUT_SEC, TELEGRAM_* и др.) — цифры без типографских пробелов "
                "и разделителей тысяч, либо обновите бот (ядро нормализует U+202F и пробелы)."
            )
        if et == "TimeoutError":
            return "Проверьте сетевую доступность и увеличьте timeout в .env для соответствующего сервиса."
        if et in {"KeyError", "AttributeError"}:
            return "Проверьте входной update/контекст: вероятно отсутствует ожидаемое поле."
        return ""

    async def _process_message(
        self,
        message: Message,
        text_override: Optional[str] = None,
        synthetic_payload: Optional[str] = None,
        cache_bypass: bool = False,
        file_context_override: Optional[Dict[str, Any]] = None,
        actor_user_id: Optional[str] = None,
    ):
        try:
            if not self._message_has_actor(message, actor_user_id):
                logger.warning(
                    "Skip message without actor user (chat_id=%s message_id=%s)",
                    getattr(message.chat, "id", None),
                    getattr(message, "message_id", None),
                )
                MONITOR.inc("input_skipped_no_actor_total")
                return
            trace = OBS.new_trace()
            OBS.stage(trace.trace_id, "input_received")
            MONITOR.inc("input_messages_total")
            # Текстовая задача (оркестратор), не отдельный Command-хендлер: показываем «печатает»
            try:
                await message.bot.send_chat_action(message.chat.id, "typing")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            au = (actor_user_id or "").strip()
            if au:
                user_id = au
            elif message.from_user is not None:
                user_id = str(message.from_user.id)
            else:
                user_id = ""
            chat_id = str(message.chat.id)
            group_id = chat_id if message.chat.type != "private" else None

            _slash_early = (
                (synthetic_payload or text_override or message.text or message.caption or "")
                .strip()
            )
            if _slash_early.startswith("/"):
                from core.command_catalog import normalize_command_token

                _slash_tok = normalize_command_token(_slash_early)
                if _slash_tok == "new":
                    from core.input_handlers.telegram_command_runners import run_new_conversation

                    await run_new_conversation(self, message)
                    elapsed_ms = OBS.finish(trace.trace_id, label="telegram_slash_new")
                    if elapsed_ms is not None:
                        MONITOR.inc("trace_finished_total")
                    return
                if await try_dispatch_inline_slash(self, message, _slash_early):
                    elapsed_ms = OBS.finish(trace.trace_id, label="telegram_slash_core")
                    if elapsed_ms is not None:
                        MONITOR.inc("trace_finished_total")
                    return
                if orchestrator_should_skip_slash(_slash_early):
                    await dispatch_core_slash_runner(self, message, _slash_early)
                    elapsed_ms = OBS.finish(trace.trace_id, label="telegram_slash_exclusive")
                    if elapsed_ms is not None:
                        MONITOR.inc("trace_finished_total")
                    return

            if text_override is None and synthetic_payload is None:
                try:
                    from core.telegram_inbound_dedup import should_skip_duplicate_message

                    if should_skip_duplicate_message(chat_id, message.message_id):
                        MONITOR.inc("telegram_inbound_dedup_skip_total")
                        logger.info(
                            "[input_layer] skip duplicate inbound message chat=%s message_id=%s",
                            chat_id,
                            message.message_id,
                        )
                        elapsed_ms = OBS.finish(trace.trace_id, label="telegram_inbound_dedup")
                        if elapsed_ms is not None:
                            MONITOR.inc("trace_finished_total")
                        return
                except Exception as e:
                    logger.debug("telegram_inbound_dedup: %s", e)

            if _env_truthy("TELEGRAM_PIPELINE_SERIALIZE_BY_CHAT", True):
                is_private = message.chat.type == "private"
                _plock = await self._pipeline_lock_for_chat(chat_id, is_private=is_private)
                async with _plock:
                    with effective_user_scope(user_id):
                        # Proactive assistance: record user activity
                        try:
                            from core.autopilot_cycle import record_user_activity
                            record_user_activity(user_id)
                        except Exception as e:
                            logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
                        await self._process_message_pipeline(
                            message,
                            trace,
                            user_id,
                            chat_id,
                            group_id,
                            text_override,
                            synthetic_payload,
                            cache_bypass,
                            file_context_override,
                        )
            else:
                with effective_user_scope(user_id):
                    # Proactive assistance: record user activity
                    try:
                        from core.autopilot_cycle import record_user_activity
                        record_user_activity(user_id)
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
                    await self._process_message_pipeline(
                        message,
                        trace,
                        user_id,
                        chat_id,
                        group_id,
                        text_override,
                        synthetic_payload,
                        cache_bypass,
                        file_context_override,
                    )

        except Exception as e:
            logger.exception("Error processing message: %s", e)
            _tb = e.__traceback__
            _where = ""
            while _tb and _tb.tb_next is not None:
                _tb = _tb.tb_next
            if _tb is not None:
                try:
                    _fn = _tb.tb_frame.f_code.co_filename
                    _ln = _tb.tb_lineno
                    _nm = _tb.tb_frame.f_code.co_name
                    _where = f"{_fn}:{_ln} ({_nm})"
                except Exception:
                    _where = ""
            _etype = type(e).__name__
            _emsg = str(e).strip()
            _hint = self._resolve_error_hint(e)
            _human = f"process_message failed: {_etype}" + (f": {_emsg}" if _emsg else "")
            if _where:
                _human += f" @ {_where}"
            if _hint:
                _human += f" | fix: {_hint}"
            record_error_event(
                "input_layer",
                _human,
                exc=e,
                extra={
                    "chat_id": message.chat.id,
                    "code": "PROCESS_MESSAGE_FAILED",
                    "where": _where,
                    "error_type": _etype,
                    "error_message": _emsg[:500],
                    "resolution_hint": _hint,
                },
            )
            try:
                uid = str(message.from_user.id) if message.from_user else ""
                admin_hint = (
                    " Для админа: /admin_health (сбои API), /admin_connectivity, /admin_logs."
                    if self._admin_module.is_admin(uid)
                    else ""
                )
                await message.answer(
                    "Ошибка обработки сообщения. Детали в логе сервера." + admin_hint,
                    **_reply_to_user_kwargs(message),
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
    async def _pipeline_lock_for_chat(self, chat_id: str, is_private: bool = False):
        """Блокировка пайплайна на один чат.
        Группы — строго последовательно (Lock).
        Личка: по умолчанию Lock (1 ход за раз) — иначе гонка за recent_messages/KV
        и ответы «на предыдущий вопрос» (регрессия bf95c38).
        TELEGRAM_PIPELINE_PRIVATE_PARALLEL=2 — только если осознанно нужна параллель.
        """
        async with self._pipeline_chat_locks_guard:
            if chat_id not in self._pipeline_chat_locks:
                parallel = 1
                if is_private:
                    try:
                        parallel = int(
                            (os.getenv("TELEGRAM_PIPELINE_PRIVATE_PARALLEL") or "1").strip()
                        )
                    except ValueError:
                        parallel = 1
                    parallel = max(1, min(4, parallel))
                if is_private and parallel > 1:
                    self._pipeline_chat_locks[chat_id] = asyncio.Semaphore(parallel)
                else:
                    self._pipeline_chat_locks[chat_id] = asyncio.Lock()
            return self._pipeline_chat_locks[chat_id]

    async def _process_message_pipeline(
        self,
        message: Message,
        trace: Any,
        user_id: str,
        chat_id: str,
        group_id: Optional[str],
        text_override: Optional[str],
        synthetic_payload: Optional[str],
        cache_bypass: bool = False,
        file_context_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        if message.chat.type == "private":
            from core.access_gate import enforce_private_dm_access

            if not await enforce_private_dm_access(
                message,
                user_id=user_id,
                bot_user_id=self._bot_user_id,
                is_admin=self._admin_module.is_admin(user_id),
                notify_new_request=self._notify_admins_new_access_request,
            ):
                return

        self._user_mgmt.ensure_user(user_id)
        if message.chat.type == "private":
            _intro_text = (text_override or message.text or message.caption or "").strip()
            _send_intro = False
            async with self._seen_private_users_lock:
                if self._should_send_private_intro(user_id, _intro_text):
                    self._seen_private_users.add(user_id)
                    self._persist_seen_private_intro_user(user_id)
                    _send_intro = True
            if _send_intro:
                await message.answer(self._greetings.private_intro())

        input_data = self._create_input(message)
        _payload_early = str(input_data.payload or "").strip()
        try:
            from core.image_gen_nl import prose_wants_new_image_project
            from core.image_edit_session import clear_image_edit_session
            from core.user_image_pending import clear_pending_images

            if prose_wants_new_image_project(_payload_early):
                _uid = str(user_id)
                _cid = str(message.chat.id)
                clear_pending_images(_uid, _cid)
                clear_image_edit_session(_uid, _cid)
                input_data.meta["image_project_reset"] = True
                if len(_payload_early) <= 120:
                    await message.answer(
                        "Новый проект: старые фото в очереди сброшены. "
                        "Отправьте актуальное фото (с подписью или отдельным текстом).",
                        **_reply_to_user_kwargs(message),
                    )
                    elapsed_ms = OBS.finish(trace.trace_id, label="telegram_image_project_reset")
                    if elapsed_ms is not None:
                        MONITOR.inc("trace_finished_total")
                    return
        except Exception as e:
            logger.debug("image_project_reset: %s", e)
        loc_msg = getattr(message, "location", None)
        if loc_msg is not None:
            lat, lon = float(loc_msg.latitude), float(loc_msg.longitude)
            tl: Dict[str, Any] = {
                "latitude": lat,
                "longitude": lon,
                "horizontal_accuracy": getattr(loc_msg, "horizontal_accuracy", None),
                "live_period": getattr(loc_msg, "live_period", None),
                "heading": getattr(loc_msg, "heading", None),
            }
            try:
                from core.geo_maps_client import geo_maps_enabled, reverse_geocode_nominatim

                if geo_maps_enabled():
                    rev = await reverse_geocode_nominatim(lat, lon)
                    if rev and rev.get("display_name"):
                        tl["display_name"] = str(rev.get("display_name"))
            except Exception as e:
                logger.debug("telegram location geocode: %s", e)
            input_data.meta["telegram_location"] = tl
            if text_override is None and synthetic_payload is None:
                if not str(input_data.payload or "").strip():
                    disp = tl.get("display_name") or ""
                    input_data.payload = (
                        "Пользователь прислал метку карты (Telegram location). Координаты: "
                        f"{lat:.6f}, {lon:.6f}"
                        + (f". Ориентир: {disp}." if disp else ".")
                        + " Кратко опиши место и предложи дальше: погода, что рядом, маршрут или геозона — по запросу."
                    )
        if message.photo:
            try:
                from core.telegram_output_guard import should_skip_duplicate_photo_turn

                ph0 = message.photo[-1]
                if should_skip_duplicate_photo_turn(
                    user_id, chat_id, str(getattr(ph0, "file_unique_id", "") or "")
                ):
                    logger.info(
                        "[input_layer] skip duplicate photo update user=%s chat=%s",
                        user_id,
                        chat_id,
                    )
                    elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_photo_dedup")
                    if elapsed_ms is not None:
                        MONITOR.inc("trace_finished_total")
                    return
            except Exception as _photo_dedupe_e:
                logger.debug("photo dedup: %s", _photo_dedupe_e)
        if message.photo or message.document or message.video:
            input_data.meta["has_telegram_attachment"] = True
        if message.document and getattr(message.document, "file_name", None):
            input_data.meta["telegram_document_filename"] = str(message.document.file_name)
        if text_override is not None:
            input_data.payload = text_override
            if message.voice:
                input_data.meta["telegram_voice_transcription"] = True
        elif synthetic_payload is not None:
            input_data.payload = synthetic_payload
        # Anti-loop: короткое «нет/стоп/отмена» прерывает pending-сценарии,
        # не уходит в LLM и не плодит fallback-петли. Системные synthetic
        # payload и сообщения с вложениями пропускаем.
        if (
            text_override is None
            and synthetic_payload is None
            and not (
                message.photo
                or message.document
                or message.video
                or message.voice
                or getattr(message, "location", None)
            )
        ):
            _payload_str = str(input_data.payload or "")
            # 🐛 Bug report handler: если пользователь нажал «Баг» и пишет описание
            try:
                from core.user_bug_report import (
                    collect_and_send,
                    has_pending,
                    pop_pending,
                    should_cancel_bug_report_pending,
                )

                if has_pending(user_id, chat_id):
                    if should_cancel_bug_report_pending(_payload_str):
                        pop_pending(user_id, chat_id)
                        logger.info(
                            "[bug_report] pending cancelled: not a bug description (len=%s)",
                            len(_payload_str or ""),
                        )
                    else:
                        pending = pop_pending(user_id, chat_id)
                    if pending:
                        un = pending.get("username", "")
                        fn = pending.get("full_name", "")
                        rid = pending.get("reply_to_msg_id", 0)
                        spawn_logged(
                            collect_and_send(
                                user_id=user_id,
                                chat_id=chat_id,
                                description=_payload_str,
                                reply_to_msg_id=rid,
                                username=un,
                                full_name=fn,
                            ),
                            label="bug_report_collect",
                        )
                        await message.answer("Спасибо! Баг-репорт отправлен разработчику 🐛", **_reply_to_user_kwargs(message))
                        OBS.stage(trace.trace_id, "bug_report_collected")
                        elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_bug_report")
                        if elapsed_ms is not None:
                            MONITOR.inc("trace_finished_total")
                        return
            except Exception as _bug_e:
                logger.debug("bug_report handler error: %s", _bug_e)
            try:
                _interrupt = try_handle_negative_interrupt(
                    text=_payload_str, user_id=user_id, chat_id=chat_id
                )
            except Exception as _e:
                logger.debug("pending_flow interrupt check failed: %s", _e)
                _interrupt = None
            if _interrupt is not None:
                _resp_text, _cleared = _interrupt
                try:
                    await message.answer(_resp_text, **_reply_to_user_kwargs(message))
                except Exception as _e:
                    logger.debug("pending interrupt reply failed: %s", _e)
                try:
                    OBS.stage(trace.trace_id, "pending_interrupt")
                    OBS.mark(trace.trace_id, f"pending_cleared:{','.join(_cleared) or 'none'}")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_pending_interrupt")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
        if group_id:
            try:
                from core.group_transcript import record_triggered_user_turn

                record_triggered_user_turn(message, input_data.payload or "")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        input_data.meta["trace_id"] = trace.trace_id
        try:
            input_data.meta["telegram_is_admin"] = bool(self._admin_module.is_admin(user_id))
        except Exception:
            input_data.meta["telegram_is_admin"] = False
        if cache_bypass:
            input_data.meta["response_cache_skip_once"] = True
        if group_id:
            try:
                await self._attach_group_chat_snapshot(message, input_data.meta)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        file_context: Optional[Dict[str, Any]] = None
        if file_context_override is not None:
            file_context = dict(file_context_override)
            input_data.meta["has_telegram_attachment"] = True
            fn0 = file_context.get("original_name")
            if fn0:
                input_data.meta["telegram_document_filename"] = str(fn0)
        else:
            file_context = await self._build_file_context(message)
            # Текст после фото: подтянуть pending (с коротким ожиданием, если фото ещё докачивается).
            if (
                file_context is None
                and isinstance(input_data.payload, str)
                and bool(input_data.payload.strip())
                and not (message.photo or message.document or message.video or message.voice)
            ):
                file_context = await self._attach_pending_image_for_text(
                    user_id=str(user_id),
                    chat_id=str(message.chat.id),
                    text=str(input_data.payload or ""),
                )
                if file_context:
                    input_data.meta["has_telegram_attachment"] = True
                    fn1 = file_context.get("original_name")
                    if fn1:
                        input_data.meta["telegram_document_filename"] = str(fn1)
                    input_data.meta["image_pending_auto_attach"] = True
                else:
                    try:
                        from core.image_gen_nl import image_gen_nl_route_enabled, prose_wants_image_edit

                        if image_gen_nl_route_enabled() and prose_wants_image_edit(
                            str(input_data.payload or "")
                        ):
                            await message.answer(
                                "Для перерисовки сначала отправьте фото "
                                "(лучше с подписью «перерисуй…» в одном сообщении).",
                                **_reply_to_user_kwargs(message),
                            )
                            elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_image_edit_no_photo")
                            if elapsed_ms is not None:
                                MONITOR.inc("trace_finished_total")
                            return
                    except Exception as e:
                        logger.debug("image_edit_no_photo hint: %s", e)
            if file_context:
                try:
                    from core.input_handlers.site_recipe_telegram import try_site_recipe_upload_early

                    if await try_site_recipe_upload_early(
                        self, message, user_id, chat_id, file_context, trace
                    ):
                        elapsed_ms = OBS.finish(trace.trace_id, label="site_recipe_upload")
                        if elapsed_ms is not None:
                            MONITOR.inc("trace_finished_total")
                        return
                except Exception as e:
                    logger.debug("site_recipe_upload early: %s", e)

        if file_context:
            _payload_now = str(input_data.payload or "").strip()
            if (
                isinstance(file_context, dict)
                and file_context.get("file_type") == "image"
                and not _payload_now
                and message.photo
                and getattr(message, "media_group_id", None)
            ):
                try:
                    from core.telegram_media_group_buffer import offer_media_group_photo

                    _buf = await offer_media_group_photo(
                        user_id=str(user_id),
                        chat_id=str(message.chat.id),
                        media_group_id=str(message.media_group_id),
                        file_context=dict(file_context),
                        on_flush=self._flush_media_group_to_pending,
                    )
                    if _buf:
                        if file_context.get("local_path"):
                            self._file_intake.cleanup(file_context.get("local_path"))
                        elapsed_ms = OBS.finish(trace.trace_id, label="telegram_media_group_buffer")
                        if elapsed_ms is not None:
                            MONITOR.inc("trace_finished_total")
                        return
                except Exception as e:
                    logger.debug("media_group_buffer: %s", e)
            if (
                isinstance(file_context, dict)
                and file_context.get("file_type") == "image"
                and _payload_now
            ):
                try:
                    from core.image_gen_multiref import (
                        merge_pending_file_contexts,
                        pending_max_photos,
                        prose_wants_multiref_pending_merge,
                    )
                    from core.user_image_pending import clear_pending_images, pop_pending_images

                    uid = str(user_id)
                    cid = str(message.chat.id)
                    if prose_wants_multiref_pending_merge(_payload_now):
                        prev = pop_pending_images(uid, cid, limit=pending_max_photos())
                        if prev:
                            merged = merge_pending_file_contexts([dict(file_context)] + prev)
                            if merged:
                                file_context = merged
                                input_data.meta["image_pending_merged"] = True
                    else:
                        cleared = clear_pending_images(uid, cid)
                        if cleared:
                            input_data.meta["image_pending_cleared"] = cleared
                except Exception as e:
                    logger.debug("merge pending into captioned photo: %s", e)
                try:
                    from core.image_edit_session import bind_image_input

                    _lp = str(file_context.get("local_path") or "").strip()
                    if _lp:
                        bind_image_input(str(user_id), str(message.chat.id), _lp)
                except Exception as e:
                    logger.debug("bind_image_input: %s", e)
            input_data.meta["file_context"] = file_context
            if isinstance(file_context, dict) and self._should_register_image_pending_after_turn(
                file_context=file_context,
                payload=_payload_now,
                meta=input_data.meta,
            ):
                self._register_image_pending_early(
                    user_id=str(user_id),
                    chat_id=str(message.chat.id),
                    file_context=file_context,
                    meta=input_data.meta,
                )
            if (
                isinstance(file_context, dict)
                and file_context.get("file_type") == "image"
                and not file_context.get("error")
                and not _payload_now
                and self._photo_only_ack_enabled()
                and message.photo
            ):
                try:
                    from core.image_gen_multiref import pending_max_photos
                    from core.user_image_pending import pending_image_count

                    n = pending_image_count(str(user_id), str(message.chat.id))
                    cap = pending_max_photos()
                    if n >= 2:
                        hint = (
                            f"Фото {n}/{cap} принято. Можно ещё до {cap} подряд, затем текст — например: "
                            "«сохрани черты лица и форму тела, сделай …» или «замени фон со 2-го фото». "
                            "(1-е = первое отправленное; 3 ракурса → точнее лицо и силуэт.) "
                            "Удобнее: все фото + подпись в одном сообщении."
                        )
                    else:
                        hint = (
                            "Фото принял. Напишите, что сделать — например: «перерисуй в аниме» "
                            "или 2–3 фото одного человека с разных ракурсов + «сохрани черты лица, …». "
                            "Новый планировочный проект — «новый проект», затем фото."
                        )
                except Exception:
                    hint = (
                        "Фото принял. Напишите, что сделать — например: «перерисуй в аниме» "
                        "(удобнее подпись к этому же фото в одном сообщении)."
                    )
                await message.answer(hint, **_reply_to_user_kwargs(message))
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_photo_ack")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
            local_path = file_context.get("local_path") if isinstance(file_context, dict) else None
            if isinstance(local_path, str) and local_path:
                try:
                    doc_info = await WORKER.submit(
                        lambda: self._document_intake.parse_file(local_path), tag="document_intake"
                    )
                    if isinstance(doc_info, dict):
                        input_data.meta["document_intake"] = doc_info
                    else:
                        input_data.meta["document_intake"] = {
                            "ok": False,
                            "error": "worker_timeout_or_failed",
                        }
                except Exception as e:
                    record_error_event("document_intake", "parse_file failed in input layer", exc=e, extra={"path": local_path})
                    input_data.meta["document_intake"] = {"ok": False, "error": str(e)}
                try:
                    from core.document_intake import intake_storable_plain
                    from core.user_document_pending import register_pending_if_enabled

                    _di = input_data.meta.get("document_intake")
                    if (
                        (message.document is not None or file_context_override is not None)
                        and isinstance(file_context, dict)
                        and file_context.get("file_type") == "document"
                        and isinstance(_di, dict)
                    ):
                        _body = intake_storable_plain(_di)
                        if _body:
                            _pid = register_pending_if_enabled(
                                user_id=str(user_id),
                                chat_id=str(message.chat.id),
                                filename=str(input_data.meta.get("telegram_document_filename") or "document"),
                                body=_body,
                            )
                            if _pid:
                                input_data.meta["pending_doc_id"] = _pid
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
                try:
                    code_info = await WORKER.submit(
                        lambda: self._code_intake.analyze_file(local_path), tag="code_intake"
                    )
                    if isinstance(code_info, dict):
                        input_data.meta["code_intake"] = code_info
                    else:
                        input_data.meta["code_intake"] = {
                            "ok": False,
                            "error": "worker_timeout_or_failed",
                        }
                except Exception as e:
                    record_error_event("code_intake", "analyze_file failed in input layer", exc=e, extra={"path": local_path})
                    if "code_intake" not in input_data.meta:
                        input_data.meta["code_intake"] = {"ok": False, "error": str(e)}
        safety = self._link_safety.check_text(input_data.payload)
        if safety.get("enabled") and safety.get("worst") in {"suspicious", "dangerous"}:
            MONITOR.inc("link_safety_flagged_total")
            if safety.get("worst") == "dangerous":
                MONITOR.inc("link_safety_dangerous_total")
            if message.chat.type != "private" and self._link_safety.mode in {"warn", "strict"}:
                await message.answer("Внимание: ссылка выглядит подозрительной. Проверь источник перед переходом.")
        trigger_kind = self._group_trigger_kind(message) if self._is_group_chat(message) else None
        flood = self.orchestrator.assess_flood_risk(
            user_id=user_id,
            chat_id=chat_id,
            text=input_data.payload,
            is_group=self._is_group_chat(message),
            is_command=bool((input_data.payload or "").strip().startswith("/")),
            is_bot_trigger_event=bool(trigger_kind),
        )
        if flood.get("blocked"):
            MONITOR.inc("flood_blocked_total")
            record_error_event(
                "anti_flood",
                "message blocked",
                extra={
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "reason": flood.get("reason"),
                    "stats": flood.get("stats"),
                    "silent": bool(flood.get("silent")),
                    "trigger_kind": trigger_kind,
                },
            )
            warn = flood.get("message")
            if warn:
                await message.answer(str(warn))
            return
        try:
            from core.telegram_recent_messages import record_incoming_message

            record_incoming_message(message, str(input_data.payload or ""))
        except Exception as e:
            logger.debug("telegram_recent_messages.incoming: %s", e)
        try:
            from core.admin_bug_runner import maybe_run_nl_bug_capture

            if await maybe_run_nl_bug_capture(
                self, message, str(input_data.payload or message.caption or "")
            ):
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="admin_bug_nl")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
        except Exception as e:
            logger.exception("maybe_run_nl_bug_capture: %s", e)
        try:
            from core.response_text_cache import build_hit_keyboard, format_answer_text, get_hit

            _hit = get_hit(user_id, chat_id, str(input_data.payload or ""), input_data.meta)
            if _hit and str(_hit.get("text") or "").strip():
                MONITOR.inc("brain_response_cache_hit_total")
                _txt = format_answer_text(str(_hit.get("text") or "").strip())
                _kb = build_hit_keyboard(str(_hit.get("module") or ""), str(_hit.get("record_id") or ""))
                await reply_text_chunks(
                    message,
                    _txt,
                    reply_markup=_kb,
                    **_reply_to_user_kwargs(message),
                )
                self._maybe_bump_guest_reply_quota(message, user_id, 1)
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
        except Exception as e:
            logger.debug("response_text_cache read: %s", e)
        sec_eval = self._security.evaluate(
            flood=flood if isinstance(flood, dict) else {},
            link_safety=safety if isinstance(safety, dict) else {},
            file_context=file_context if isinstance(file_context, dict) else {},
        )
        if isinstance(sec_eval, dict):
            lvl = sec_eval.get("level")
            if lvl == "warning":
                MONITOR.inc("security_warning_total")
            elif lvl == "high_risk":
                MONITOR.inc("security_high_risk_total")

        # Mem0: сохраняем сообщение (для лёгкого приватного читчата можно пропустить — быстрее)
        _meta0 = input_data.meta if isinstance(input_data.meta, dict) else {}
        _ds_mem0: dict = {}
        _bs0 = getattr(self.orchestrator, "behavior_store", None)
        if _bs0 and user_id:
            try:
                _rec0 = _bs0.load(user_id, group_id)
                if isinstance(_rec0.get("dialogue_state"), dict):
                    _ds_mem0 = _rec0["dialogue_state"]
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        _skip_mem0_user = (
            brain_fast_chitchat_eligible(
                input_data.payload or "",
                group_id,
                file_context if isinstance(file_context, dict) else None,
                _meta0.get("document_intake"),
                _meta0.get("code_intake"),
            )
            and not private_dm_chitchat_continuity_override(group_id, _ds_mem0, input_data.payload or "")
            and _env_truthy("INPUT_SKIP_MEM0_ON_FAST_CHITCHAT", True)
        )
        if self.mem0_memory and not _skip_mem0_user:
            try:
                await self.mem0_memory.on_user_message(user_id, input_data.payload)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        OBS.mark(trace.trace_id, "pre_plan")

        if (input_data.payload or "").strip().startswith("/"):
            _slash_text = input_data.payload or ""
            if await try_dispatch_inline_slash(self, message, _slash_text):
                self._maybe_bump_guest_reply_quota(message, user_id, 1)
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
            if await try_dispatch_admin_slash(self, message, _slash_text):
                self._maybe_bump_guest_reply_quota(message, user_id, 1)
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_admin_slash")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
            if orchestrator_should_skip_slash(_slash_text):
                if not await try_dispatch_inline_slash(self, message, _slash_text):
                    await dispatch_core_slash_runner(self, message, _slash_text)
                self._maybe_bump_guest_reply_quota(message, user_id, 1)
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_slash_exclusive")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return

        is_group = self._is_group_chat(message)
        use_keepalive = (is_group and _env_truthy("GROUP_TYPING_KEEPALIVE", True)) or (
            (not is_group) and _env_truthy("PRIVATE_TYPING_KEEPALIVE", True)
        )
        typing_keepalive: Optional[asyncio.Task] = None
        if use_keepalive:
            if is_group:
                raw_iv = os.getenv("GROUP_TYPING_INTERVAL_SEC") or "4.5"
            else:
                raw_iv = (
                    os.getenv("PRIVATE_TYPING_INTERVAL_SEC")
                    or os.getenv("GROUP_TYPING_INTERVAL_SEC")
                    or "4.5"
                )
            interval = parse_loose_float(raw_iv, 4.5)
            interval = max(2.0, min(interval, 8.0))

            async def _keep_typing() -> None:
                while True:
                    try:
                        await message.bot.send_chat_action(message.chat.id, "typing")
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
                    await asyncio.sleep(interval)

            typing_keepalive = asyncio.create_task(_keep_typing())

        progress_mid: Optional[int] = None
        _anti_intrusion_silent = False
        try:
            plan = self.orchestrator.plan(input_data, user_id, group_id)
            OBS.mark(trace.trace_id, "post_plan")
            OBS.stage(trace.trace_id, "planned")

            _ctx_plan = (plan.steps[0].args or {}).get("context") if plan.steps else {}
            _ff_plan = _ctx_plan.get("facts_flow") if isinstance(_ctx_plan, dict) else {}
            _payload_str_plan = str(input_data.payload or "")
            _facts_shortcut_outputs = None
            try:
                from core.models import Output
                from core.user_facts import (
                    facts_save_confirm_lane_eligible,
                    try_facts_shortcut_payload,
                )

                _recent_plan = (
                    _ctx_plan.get("recent_messages") if isinstance(_ctx_plan, dict) else None
                )
                _persisted_aff = self.orchestrator.behavior_store.load(user_id, group_id) or {}
                _idle_ack = None
                if _facts_shortcut_outputs is None:
                    _idle_ack = try_facts_shortcut_payload(
                        _payload_str_plan,
                        _ff_plan,
                        recent_dialogue=_recent_plan,
                        persisted=_persisted_aff if isinstance(_persisted_aff, dict) else None,
                    )
                if _idle_ack:
                    _facts_shortcut_outputs = [
                        Output(
                            type="text",
                            payload=_idle_ack,
                            meta={"module": "user_facts", "facts_idle_ack": True},
                        )
                    ]
                try:
                    from core.brain.text_helpers import (
                        affirmative_overrides_fact_confirmation,
                        looks_like_affirmative_short,
                    )
                    from core.brain_own_turn import planner_direct_allowed
                    from core.news_reply import try_affirmative_search_reply_sync
                    from core.user_facts import has_pending_facts_confirmation

                    _facts_committed_this_turn = (
                        isinstance(_ff_plan, dict) and bool(_ff_plan.get("committed_facts_this_turn"))
                    )
                    if (
                        _facts_shortcut_outputs is None
                        and not _facts_committed_this_turn
                        and not has_pending_facts_confirmation(_persisted_aff)
                        and planner_direct_allowed("affirmative_search")
                        and looks_like_affirmative_short(_payload_str_plan)
                        and affirmative_overrides_fact_confirmation(
                            _payload_str_plan,
                            recent_dialogue=_recent_plan,
                            persisted=_persisted_aff if isinstance(_persisted_aff, dict) else None,
                        )
                    ):
                        _aff_body = try_affirmative_search_reply_sync(
                            _payload_str_plan,
                            persisted=_persisted_aff if isinstance(_persisted_aff, dict) else None,
                            user_id=str(user_id),
                            recent_dialogue=_recent_plan,
                        )
                        if _aff_body and str(_aff_body).strip():
                            MONITOR.inc("brain_affirmative_search_short_circuit_total")
                            _facts_shortcut_outputs = [
                                Output(
                                    type="text",
                                    payload=str(_aff_body).strip(),
                                    meta={
                                        "module": "news_reply",
                                        "affirmative_search": True,
                                    },
                                )
                            ]
                except Exception as e:
                    logger.debug("affirmative_search input_layer: %s", e)
                if _facts_shortcut_outputs is None and facts_save_confirm_lane_eligible(_ff_plan):
                    from core.clarification_inline_keyboard import fact_confirmation_keyboard_rows
                    from core.telegram_inline_meta import META_KEY

                    _facts_shortcut_outputs = [
                        Output(
                            type="text",
                            payload=str(_ff_plan.get("confirmation_prompt") or "").strip(),
                            meta={
                                "module": "user_facts",
                                "confirmation": True,
                                META_KEY: fact_confirmation_keyboard_rows(),
                            },
                        )
                    ]
            except Exception as e:
                logger.debug("facts_confirm_lane: %s", e)

            reply_markup = None
            if plan.steps:
                mk0 = plan.steps[0].module_name
                keys: List[str] = []
                if mk0 and mk0 != "__fallback__":
                    keys.append(mk0)
                extra = (os.getenv("TELEGRAM_EXTRA_REPLY_BUTTON_MODULES") or "").strip()
                if mk0 in ("chat-orchestrator", "__fallback__") and extra:
                    for part in extra.split(","):
                        k = part.strip()
                        if k and k not in keys:
                            keys.append(k)
                if keys:
                    reply_markup = merge_manifest_buttons_keyboards(self.plugin_registry, keys)
                ctx0 = (plan.steps[0].args or {}).get("context") or {}
                hooks = ctx0.get("typing_hooks") or {}
                if hooks.get("enabled"):
                    try:
                        await message.bot.send_chat_action(message.chat.id, "typing")
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            if _telegram_progress_eligible(message, plan):
                try:
                    pm = await message.answer(
                        _telegram_progress_initial_text(),
                        **_reply_to_user_kwargs(message),
                    )
                    progress_mid = pm.message_id
                    telegram_progress_arm(message.bot, message.chat.id, progress_mid)
                    uid_prog = str(message.from_user.id) if message.from_user else ""
                    is_admin_prog = (
                        self._admin_module.is_admin(uid_prog) if uid_prog else False
                    )
                    if telegram_stream_should_bind(
                        user_text=str(input_data.payload or ""),
                        is_group=is_group,
                        user_id=uid_prog,
                        is_admin=is_admin_prog,
                    ):
                        await register_chat_cancel(str(message.chat.id))
                        telegram_stream_bind_progress(
                            message.bot, message.chat.id, progress_mid, uid_prog
                        )
                        from core.telegram_stream_reasoning import admin_stream_reasoning_effective

                        arm_admin_stream_reasoning(
                            admin_stream_reasoning_effective(is_admin=is_admin_prog)
                        )
                    telegram_progress_seed_from_user_text(str(input_data.payload or ""))
                    try:
                        await telegram_progress_pulse(_telegram_progress_initial_text(), force=True)
                        await telegram_progress_start_etc_refresh()
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
                except Exception:
                    progress_mid = None

            if _facts_shortcut_outputs is not None:
                outputs = _facts_shortcut_outputs
                MONITOR.inc("brain_facts_confirm_lane_total")
                try:
                    _assist = "\n\n".join(
                        str(o.payload or "").strip()
                        for o in outputs
                        if o.type == "text" and str(o.payload or "").strip()
                    ).strip()
                    _turn_meta: Dict[str, Any] = {}
                    if isinstance(input_data.meta, dict) and input_data.meta.get("message_id") is not None:
                        try:
                            _turn_meta["telegram_message_id"] = int(input_data.meta.get("message_id"))
                        except (TypeError, ValueError):
                            pass
                    if isinstance(input_data.meta, dict) and input_data.meta.get("telegram_message_date_unix") is not None:
                        _turn_meta["telegram_message_date_unix"] = input_data.meta.get(
                            "telegram_message_date_unix"
                        )
                    self.orchestrator.behavior_store.update_after_turn(
                        user_id,
                        group_id,
                        _payload_str_plan,
                        _assist,
                        turn_meta=_turn_meta or None,
                    )
                except Exception as e:
                    logger.debug("facts_confirm_lane persist: %s", e)
            else:
                outputs = await self.orchestrator.execute_plan(plan, user_id, group_id)
            try:
                _ctx0 = (plan.steps[0].args or {}).get("context") if plan.steps else {}
                if isinstance(_ctx0, dict) and _ctx0.get("_outputs_finalized"):
                    _anti_intrusion_silent = bool(_ctx0.get("_output_silent_skip"))
                    if isinstance(input_data.meta, dict) and isinstance(_ctx0.get("_scenario_hits"), list):
                        input_data.meta["scenario_hits"] = _ctx0["_scenario_hits"]
                else:
                    from core.scenario_engine import (
                        apply_post_execute,
                        forecast_from_dict,
                        merge_hits,
                    )

                    _sf = forecast_from_dict(
                        (input_data.meta or {}).get("scenario_forecast")
                        if isinstance(input_data.meta, dict)
                        else None
                    )
                    outputs, _post_hits, _anti_intrusion_silent = apply_post_execute(
                        outputs, str(input_data.payload or ""), _sf
                    )
                    if isinstance(input_data.meta, dict) and (_sf.hits or _post_hits):
                        input_data.meta["scenario_hits"] = merge_hits(_sf, _post_hits)
            except Exception as _sc_e:
                logger.debug("scenario_engine post: %s", _sc_e)
                outputs, _anti_intrusion_silent = _apply_anti_intrusion_guard(
                    str(input_data.payload or ""), outputs
                )
        finally:
            telegram_progress_disarm()
            # Не удалять progress здесь: ответ ещё не отправлен — иначе «Думаю…» исчезает без текста (INCIDENT stream off + brain non-stream).
            try:
                await clear_chat_cancel(str(message.chat.id))
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            telegram_stream_disarm()
            disarm_admin_stream_reasoning()
            if typing_keepalive is not None:
                typing_keepalive.cancel()
                try:
                    await typing_keepalive
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        OBS.mark(trace.trace_id, "post_execute")
        MONITOR.inc("execute_plan_calls")
        OBS.stage(trace.trace_id, "executed")

        if gemma_core_log_full():
            logger.info(
                "[CORE] pipeline plan_mode=%s steps=%s outputs=%s trace=%s",
                plan.mode,
                [s.module_name for s in plan.steps],
                [(o.type, len(str(o.payload or ""))) for o in outputs],
                trace.trace_id[:12],
                extra={
                    "gemma_event": "pipeline_executed",
                    "trace_id": trace.trace_id,
                    "plan_mode": plan.mode,
                    "step_modules": [s.module_name for s in plan.steps],
                },
            )

        try:
            from core.telegram_output_guard import keep_single_best_text_output

            outputs = keep_single_best_text_output(
                outputs, str(input_data.payload or "")
            )
        except Exception as e:
            logger.debug("keep_single_best_text_output: %s", e)

        if not outputs:
            if _anti_intrusion_silent:
                if isinstance(file_context, dict) and file_context.get("local_path"):
                    self._file_intake.cleanup(file_context.get("local_path"))
                elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline_anti_intrusion_silent")
                if elapsed_ms is not None:
                    MONITOR.inc("trace_finished_total")
                return
            await message.answer("Я не смог обработать запрос.", **_reply_to_user_kwargs(message))
            self._maybe_bump_guest_reply_quota(message, user_id, 1)
            return

        first_text_kb = True
        # Найти последний текстовый output для фидбек-кнопок
        _last_text_idx = -1
        for idx, out in enumerate(outputs):
            if out.type == "text" and str(out.payload or "").strip() and len(str(out.payload or "").strip()) > 40:
                _last_text_idx = idx

        for i, out in enumerate(outputs):
            try:
                if (out.meta or {}).get("reason") == "math_ambiguous":
                    from core.math_clarify_pending import set_pending

                    set_pending(user_id, chat_id, str(input_data.payload or ""))
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            meta_kb = inline_markup_from_meta(out.meta)
            use_kb = meta_kb
            if (
                not use_kb
                and first_text_kb
                and out.type == "text"
                and str(out.payload or "").strip()
                and not (out.meta or {}).get("confirmation")
            ):
                use_kb = reply_markup
            if use_kb:
                first_text_kb = False
            # Feedback buttons: только на последнем текстовом output
            if i == _last_text_idx:
                try:
                    from core.feedback_buttons import merge_with_reply_markup
                    use_kb = merge_with_reply_markup(use_kb, user_id)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            if out.type == "text":
                meta_ut = out.meta if isinstance(out.meta, dict) else {}
                if not str(meta_ut.get("user_text") or "").strip():
                    meta_ut = {**meta_ut, "user_text": str(input_data.payload or "")}
                if i == _last_text_idx:
                    meta_ut["_mode_footer"] = True
                    meta_ut.setdefault("trace_id", trace.trace_id)
                    if isinstance(input_data.meta, dict) and "telegram_is_admin" in input_data.meta:
                        meta_ut["telegram_is_admin"] = bool(input_data.meta.get("telegram_is_admin"))
                    # Для приватного чата group_id=None; важно для корректной загрузки слота/персиста в mode footer.
                    meta_ut["group_id"] = group_id
                    _pm = (plan.steps[0].module_name if plan.steps else "") or ""
                    if _pm:
                        meta_ut["plan_module"] = _pm
                    _pctx = (plan.steps[0].args or {}).get("context") if plan.steps else {}
                    if isinstance(_pctx, dict):
                        _ds = _pctx.get("dialogue_state") if isinstance(_pctx.get("dialogue_state"), dict) else {}
                        if isinstance(_ds, dict) and _ds.get("last_intent"):
                            meta_ut["route_intent"] = str(_ds.get("last_intent") or "")
                        if _pctx.get("brain_profile"):
                            meta_ut.setdefault("route_profile", str(_pctx.get("brain_profile") or ""))
                        if _pctx.get("planner_bypass"):
                            meta_ut["route_pre_llm"] = str(_pctx.get("planner_bypass") or "")
                        if _pctx.get("skill_name"):
                            meta_ut["route_skill"] = str(_pctx.get("skill_name") or "")
                out.meta = meta_ut
            _edited = await self._send_output(
                message, out, reply_markup=use_kb, progress_message_id=progress_mid
            )
            if _edited:
                progress_mid = None
        if progress_mid is not None and not telegram_stream_has_delivery():
            try:
                await message.bot.delete_message(message.chat.id, progress_mid)
            except Exception as e:
                logger.debug("progress_delete_after_answer: %s", e)
        try:
            _log_telegram_turn_summary(
                trace_id=trace.trace_id,
                user_id=user_id,
                chat_id=chat_id,
                user_text=str(input_data.payload or ""),
                plan=plan,
                outputs=outputs,
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        self._maybe_bump_guest_reply_quota(message, user_id, len(outputs))
        try:
            from core.response_text_cache import maybe_store

            _mod0 = (plan.steps[0].module_name if plan.steps else "") or ""
            maybe_store(
                user_id=user_id,
                chat_id=chat_id,
                replay_payload=str(input_data.payload or ""),
                input_meta=input_data.meta if isinstance(input_data.meta, dict) else {},
                module_name=_mod0,
                outputs=outputs,
            )
        except Exception as e:
            logger.debug("response_text_cache store: %s", e)
        if group_id and outputs:
            try:
                from core.group_transcript import record_assistant_reply

                for out in outputs:
                    if out.type == "text" and str(out.payload or "").strip():
                        record_assistant_reply(group_id, str(out.payload))
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
        try:
            from core.telegram_recent_messages import record_bot_reply_text

            _tail_parts = [
                str(o.payload or "")
                for o in outputs
                if o.type == "text" and str(o.payload or "").strip()
            ]
            if _tail_parts:
                record_bot_reply_text(message, "\n\n".join(_tail_parts))
        except Exception as e:
            logger.debug("record_bot_reply_text: %s", e)
        if self._voice.enabled and self._voice.tts_enabled and self._voice.reply_enabled and outputs:
            try:
                _ = await self._voice.tts(str(outputs[0].payload))
            except Exception as e:
                record_error_event("voice", "tts failed", exc=e, extra={"user_id": user_id})
        try:
            self._sync_image_edit_session_from_turn(
                user_id=str(user_id),
                chat_id=str(message.chat.id),
                file_context=file_context if isinstance(file_context, dict) else None,
                outputs=outputs,
            )
        except Exception as e:
            logger.debug("sync_image_edit_session: %s", e)
        if isinstance(file_context, dict) and file_context.get("local_path"):
            try:
                if (
                    file_context.get("file_type") == "image"
                    and not file_context_override
                    and not input_data.meta.get("image_pending_registered_early")
                    and self._should_register_image_pending_after_turn(
                        file_context=file_context,
                        payload=str(input_data.payload or ""),
                        meta=input_data.meta if isinstance(input_data.meta, dict) else {},
                    )
                ):
                    register_pending_image(str(user_id), str(message.chat.id), file_context)
            except Exception as e:
                logger.debug("pending_image register: %s", e)
            self._file_intake.cleanup(file_context.get("local_path"))
        elapsed_ms = OBS.finish(trace.trace_id, label="telegram_pipeline")
        if elapsed_ms is not None:
            MONITOR.inc("trace_finished_total")

    # ============================================================
    #   INPUT CREATION
    # ============================================================
    @staticmethod
    def _image_gen_pending_wait_ms() -> int:
        raw = (os.getenv("IMAGE_GEN_PENDING_WAIT_MS") or "2500").strip()
        try:
            ms = int(raw)
        except ValueError:
            ms = 2500
        return max(0, min(ms, 8000))

    @staticmethod
    def _photo_only_ack_enabled() -> bool:
        raw = os.getenv("IMAGE_PHOTO_ONLY_ACK")
        if raw is None:
            return True
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _should_register_image_pending_after_turn(
        *,
        file_context: Dict[str, Any],
        payload: str,
        meta: Dict[str, Any],
    ) -> bool:
        """Не класть фото обратно в pending после фото+подпись (иначе склеиваются разные планы)."""
        if str(file_context.get("file_type") or "").strip().lower() != "image":
            return False
        text = str(payload or "").strip()
        if not text:
            return True
        if meta.get("image_pending_auto_attach"):
            return False
        return False

    async def _attach_pending_image_for_text(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
    ) -> Optional[Dict[str, Any]]:
        """Подтянуть фото: multiref из pending, правка — из сессии, иначе одно последнее pending."""
        try:
            from core.image_gen_nl import (
                image_gen_nl_route_enabled,
                prose_wants_image_edit,
                prose_wants_new_image_project,
                prose_wants_image_pending_followup,
                text_eligible_for_pending_image_attach,
            )

            if not image_gen_nl_route_enabled() or not text_eligible_for_pending_image_attach(text):
                return None
        except Exception:
            return None
        uid = str(user_id)
        cid = str(chat_id)
        try:
            from core.image_edit_session import clear_image_edit_session, file_context_for_session_edit
            from core.image_gen_multiref import (
                merge_pending_file_contexts,
                pending_max_photos,
                prose_wants_multiref_pending_merge,
            )
            from core.user_image_pending import clear_pending_images

            if prose_wants_new_image_project(text):
                clear_pending_images(uid, cid)
                clear_image_edit_session(uid, cid)
                return None
        except Exception:
            pass

        wait_ms = self._image_gen_pending_wait_ms()
        import asyncio
        import time

        deadline = time.monotonic() + (wait_ms / 1000.0)
        while True:
            if has_pending_image(uid, cid):
                break
            if wait_ms <= 0 or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)

        try:
            from core.image_gen_multiref import (
                merge_pending_file_contexts,
                pending_max_photos,
                prose_wants_multiref_pending_merge,
            )
            from core.image_gen_nl import prose_wants_image_edit, prose_wants_image_pending_followup
            from core.image_edit_session import file_context_for_session_edit

            if prose_wants_multiref_pending_merge(text):
                pending_fcs = pop_pending_images(uid, cid, limit=pending_max_photos())
                merged = merge_pending_file_contexts(pending_fcs)
                if merged:
                    return merged
                if pending_fcs and isinstance(pending_fcs[0], dict):
                    return dict(pending_fcs[0])

            if prose_wants_image_pending_followup(text) or prose_wants_image_edit(text):
                session_fc = file_context_for_session_edit(uid, cid)
                if session_fc:
                    return session_fc

            pending_one = pop_pending_images(uid, cid, limit=1)
            if pending_one and isinstance(pending_one[0], dict) and pending_one[0].get("local_path"):
                return dict(pending_one[0])
            return file_context_for_session_edit(uid, cid)
        except Exception:
            pass
        pending_fcs = pop_pending_images(uid, cid, limit=1)
        if pending_fcs and isinstance(pending_fcs[0], dict):
            return dict(pending_fcs[0])
        return None

    async def _flush_media_group_to_pending(
        self,
        user_id: str,
        chat_id: str,
        media_group_id: str,
        items: List[Dict[str, Any]],
    ) -> None:
        from core.user_image_pending import clear_pending_images, register_pending_image

        uid = str(user_id)
        cid = str(chat_id)
        clear_pending_images(uid, cid)
        for fc in items:
            if isinstance(fc, dict) and fc.get("local_path"):
                register_pending_image(uid, cid, fc)
        n = len(items)
        hint = (
            f"Альбом из {n} фото принят. "
            "Напишите задачу одним сообщением — для нескольких референсов укажите «со 2-го фото» или «3 ракурса»."
        )
        try:
            await self.bot.send_message(int(chat_id), hint)
        except Exception as e:
            logger.debug("media_group ack failed: %s", e)

    @staticmethod
    def _sync_image_edit_session_from_turn(
        *,
        user_id: str,
        chat_id: str,
        file_context: Optional[Dict[str, Any]],
        outputs: List[Output],
    ) -> None:
        from core.image_edit_session import bind_image_input, bind_image_output

        uid = str(user_id)
        cid = str(chat_id)
        for out in outputs or []:
            meta = out.meta if isinstance(out.meta, dict) else {}
            if str(meta.get("module") or "") != "image_generator":
                continue
            out_path = str(meta.get("image_output_path") or "").strip()
            if out_path:
                bind_image_output(uid, cid, out_path)
        if isinstance(file_context, dict) and file_context.get("file_type") == "image":
            in_path = str(file_context.get("local_path") or "").strip()
            if in_path:
                bind_image_input(uid, cid, in_path)

    @staticmethod
    def _register_image_pending_early(
        *,
        user_id: str,
        chat_id: str,
        file_context: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> None:
        if file_context.get("error"):
            return
        if file_context.get("file_type") != "image":
            return
        if not str(file_context.get("local_path") or "").strip():
            return
        try:
            register_pending_image(user_id, chat_id, file_context)
            meta["image_pending_registered_early"] = True
        except Exception as e:
            logger.debug("pending_image register early: %s", e)

    async def _build_file_context(self, message: Message) -> Optional[Dict[str, Any]]:
        if not self._file_intake.enabled:
            return None
        file_id = ""
        file_type = "other"
        mime_type = ""
        size = 0
        original_name = ""
        if message.photo:
            ph = message.photo[-1]
            file_id = ph.file_id
            file_type = "image"
            mime_type = "image/jpeg"
            size = getattr(ph, "file_size", 0) or 0
            original_name = f"{file_id}.jpg"
        elif message.document:
            d = message.document
            file_id = d.file_id
            file_type = "image" if (d.mime_type or "").startswith("image/") else "document"
            mime_type = d.mime_type or ""
            size = getattr(d, "file_size", 0) or 0
            original_name = d.file_name or file_id
        elif message.video:
            v = message.video
            file_id = v.file_id
            file_type = "video"
            mime_type = v.mime_type or "video/mp4"
            size = getattr(v, "file_size", 0) or 0
            original_name = f"{file_id}.mp4"
        elif message.voice:
            vc = message.voice
            file_id = vc.file_id
            file_type = "voice"
            mime_type = vc.mime_type or "audio/ogg"
            size = getattr(vc, "file_size", 0) or 0
            original_name = f"{file_id}.ogg"
        if not file_id:
            return None
        if not self._file_intake.enforce_size_limit(file_type, int(size)):
            record_error_event(
                "file_intake",
                "size limit exceeded",
                extra={"file_type": file_type, "size": size, "chat_id": message.chat.id},
                severity="warning",
            )
            return {"error": "size_limit_exceeded", "file_type": file_type, "size": size}
        fc = FileContext(
            file_id=file_id,
            file_type=file_type,
            mime_type=mime_type,
            size=int(size),
            original_name=original_name,
            chat_id=str(message.chat.id),
            user_id=str(message.from_user.id) if message.from_user else "",
        )
        try:
            fc.local_path = await self._file_intake.download_file(self.bot, fc.file_id, fc.original_name)
        except Exception as e:
            record_error_event("file_intake", "download failed", exc=e, extra={"chat_id": message.chat.id, "file_type": file_type})
        return fc.to_dict()

    def _create_input(self, message: Message) -> Input:
        payload = message.text or message.caption or ""
        msg_type = "text"
        if message.photo:
            msg_type = "image"
        elif message.voice:
            msg_type = "audio"
        elif message.video:
            msg_type = "video"
        elif message.document:
            msg_type = "file"
        meta: Dict[str, Any] = {
            "user_id": (message.from_user.id if message.from_user else ""),
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "source": "telegram",
            "channel": "telegram",
        }
        try:
            td = getattr(message, "date", None)
            if isinstance(td, datetime):
                if td.tzinfo is None:
                    td = td.replace(tzinfo=timezone.utc)
                meta["telegram_message_date_unix"] = int(td.timestamp())
                meta["telegram_message_date_iso"] = td.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, ValueError, TypeError):
            pass
        tr_parts: List[str] = []
        if getattr(message, "forward_origin", None) is not None:
            meta["telegram_has_forward"] = True
        fp = telegram_forward_preamble(message)
        if fp:
            tr_parts.append(fp)
            body_here = (message.text or message.caption or "").strip()
            if len(body_here) <= 28:
                tr_parts.append(
                    "Пересылка: в этом апдейте может быть только короткий комментарий к пересланному сообщению; "
                    "полный текст пересланного поста не всегда попадает в поле текста (медиа, ограничения канала). "
                    "Опирайся на цепочку **ответа (reply)** выше и на recent_dialogue; если смысла не хватает — скажи честно и попроси цитату или переслать с подписью."
                )
        rc = telegram_reply_context_from_message(message, bot_user_id=self._bot_user_id)
        if rc:
            tr_parts.append(rc)
        if tr_parts:
            meta["telegram_reply_context"] = "\n\n".join(tr_parts)
        return Input(
            type=msg_type,  # type: ignore[arg-type]
            payload=payload,
            meta=meta,
        )

    # ============================================================
    #   SEND OUTPUT
    # ============================================================
    async def _send_output(
        self,
        message: Message,
        output: Output,
        reply_markup=None,
        *,
        progress_message_id: Optional[int] = None,
    ) -> bool:
        """Возвращает True, если ответ показан редактированием progress-сообщения."""
        try:
            env = self._response_adapter.from_output(output)
            tgp = self._response_adapter.to_telegram_payload(env)
            image_path = ""
            file_path = ""
            loc_lat: Optional[float] = None
            loc_lon: Optional[float] = None
            for att in tgp.get("attachments", []):
                if isinstance(att, dict) and att.get("type") == "location":
                    try:
                        loc_lat = float(att.get("latitude"))
                        loc_lon = float(att.get("longitude"))
                    except (TypeError, ValueError):
                        loc_lat, loc_lon = None, None
                    break
            if loc_lat is not None and loc_lon is not None:
                try:
                    await message.answer_location(loc_lat, loc_lon, **_reply_to_user_kwargs(message))
                except Exception as e:
                    record_error_event("geo", "answer_location failed", exc=e)
            for att in tgp.get("attachments", []):
                if isinstance(att, dict) and att.get("type") == "file" and isinstance(att.get("path"), str):
                    file_path = att.get("path")
                    break
            if isinstance(file_path, str) and file_path:
                try:
                    from aiogram.types import FSInputFile

                    raw_doc_txt = str(tgp.get("text", "") or "")
                    cap: Optional[str] = None
                    doc_overflow = ""
                    if raw_doc_txt.strip():
                        if len(raw_doc_txt) <= _TELEGRAM_CAPTION_MAX:
                            cap = raw_doc_txt
                        else:
                            cap = raw_doc_txt[:_TELEGRAM_CAPTION_MAX]
                            doc_overflow = raw_doc_txt[_TELEGRAM_CAPTION_MAX:].lstrip()
                    await message.answer_document(
                        FSInputFile(file_path),
                        caption=cap,
                        **_reply_to_user_kwargs(message),
                    )
                    if doc_overflow:
                        await reply_text_chunks(message, doc_overflow, **_reply_to_user_kwargs(message))
                    return
                except Exception as e:
                    record_error_event("file_tools", "send file attachment failed", exc=e, extra={"path": file_path})
            for att in tgp.get("attachments", []):
                if isinstance(att, dict) and att.get("type") == "image" and isinstance(att.get("path"), str):
                    image_path = att.get("path")
                    break
            image_url = ""
            for att in tgp.get("attachments", []):
                if isinstance(att, dict) and att.get("type") == "image" and isinstance(att.get("url"), str):
                    image_url = att.get("url")
                    break
            if isinstance(image_url, str) and image_url.startswith("http"):
                try:
                    from aiogram.types import URLInputFile

                    cap_att = None
                    for att in tgp.get("attachments", []):
                        if isinstance(att, dict) and att.get("type") == "image":
                            cap_att = att.get("caption")
                            break
                    img_cap = str(cap_att or "").strip() or None
                    await message.answer_photo(
                        URLInputFile(image_url, filename="news.jpg"),
                        caption=img_cap,
                        **_reply_to_user_kwargs(message),
                    )
                    txt_after = str(tgp.get("text", "") or "").strip()
                    if txt_after:
                        await reply_text_chunks(message, txt_after, **_reply_to_user_kwargs(message))
                    return
                except Exception as e:
                    record_error_event(
                        "image_tools",
                        "send image url attachment failed",
                        exc=e,
                        extra={"url": image_url[:120]},
                    )
            if isinstance(image_path, str) and image_path:
                try:
                    from aiogram.types import FSInputFile

                    raw_img_txt = str(tgp.get("text", "") or "")
                    img_cap: Optional[str] = None
                    img_overflow = ""
                    if raw_img_txt.strip():
                        if len(raw_img_txt) <= _TELEGRAM_CAPTION_MAX:
                            img_cap = raw_img_txt
                        else:
                            img_cap = raw_img_txt[:_TELEGRAM_CAPTION_MAX]
                            img_overflow = raw_img_txt[_TELEGRAM_CAPTION_MAX:].lstrip()
                    await message.answer_photo(
                        FSInputFile(image_path),
                        caption=img_cap,
                        **_reply_to_user_kwargs(message),
                    )
                    if img_overflow:
                        await reply_text_chunks(message, img_overflow, **_reply_to_user_kwargs(message))
                    return
                except Exception as e:
                    record_error_event("image_tools", "send image attachment failed", exc=e, extra={"path": image_path})
            txt = str(tgp.get("text", "") or "").strip()
            _stream_delivered_early = telegram_stream_take_delivery()
            if _stream_delivered_early is not None:
                txt = str(_stream_delivered_early).strip()
            try:
                from core.brain.response_finalize import finalize_user_reply

                _ut_fin = ""
                if isinstance(getattr(output, "meta", None), dict):
                    _ut_fin = str(output.meta.get("user_text") or output.meta.get("payload") or "")
                txt = finalize_user_reply(txt, user_text=_ut_fin) or txt
                if isinstance(output.meta, dict):
                    output.meta["_user_reply_finalized"] = True
            except Exception as e:
                logger.debug('%s optional failed: %s', 'input_layer', e, exc_info=True)
            try:
                from core.scenario_engine import apply_pre_send

                txt, _pre_hits = apply_pre_send(
                    txt,
                    user_text=_ut_fin,
                    output_meta=output.meta if isinstance(output.meta, dict) else None,
                )
                try:
                    from core.heavy_response_reflection import (
                        refine_heavy_reply,
                        should_reflect_heavy_turn,
                    )

                    _prof = ""
                    _tier = ""
                    _tool_steps = 0
                    if isinstance(output.meta, dict):
                        _prof = str(
                            output.meta.get("brain_profile")
                            or output.meta.get("router_profile")
                            or ""
                        )
                        _tier = str(output.meta.get("task_tier") or "")
                        _tool_steps = int(output.meta.get("tool_steps") or 0)
                    _pre_meta = [
                        {"id": h.id, "action": h.action, "severity": h.severity}
                        for h in (_pre_hits or [])
                    ]
                    if should_reflect_heavy_turn(
                        user_text=_ut_fin,
                        reply=txt,
                        profile=_prof,
                        task_tier=_tier,
                        scenario_pre_hits=_pre_meta,
                        tool_steps=_tool_steps,
                        output_meta=output.meta if isinstance(output.meta, dict) else None,
                    ):
                        _uid_ref = str(message.from_user.id) if message.from_user else ""
                        _sid_ref = ""
                        if isinstance(output.meta, dict):
                            _sid_ref = str(
                                output.meta.get("llm_session_id")
                                or output.meta.get("session_id")
                                or ""
                            )
                            if not _sid_ref:
                                _kv = output.meta.get("kv_session_debug")
                                if isinstance(_kv, dict):
                                    _sid_ref = str(_kv.get("session_id") or "")
                        txt = await refine_heavy_reply(
                            user_text=_ut_fin,
                            reply=txt,
                            profile=_prof,
                            user_id=_uid_ref,
                            session_id=_sid_ref,
                        )
                        if isinstance(output.meta, dict):
                            output.meta["reflection_heavy"] = True
                except Exception as _rh_e:
                    logger.debug("heavy_response_reflection: %s", _rh_e)
                if _pre_hits and isinstance(output.meta, dict):
                    prev = output.meta.get("scenario_pre_send") or []
                    if isinstance(prev, list):
                        output.meta["scenario_pre_send"] = prev + [
                            {"id": h.id, "action": h.action} for h in _pre_hits
                        ]
                    else:
                        output.meta["scenario_pre_send"] = [
                            {"id": h.id, "action": h.action} for h in _pre_hits
                        ]
            except Exception as _ps_e:
                logger.debug("scenario pre_send: %s", _ps_e)
            try:
                from core.reply_mode_footer import (
                    append_mode_footer,
                    build_mode_footer_fields,
                    footer_visible_for_user,
                    should_skip_mode_footer,
                )

                _om_f = output.meta if isinstance(output.meta, dict) else {}
                if (
                    _om_f.get("_mode_footer")
                    and not should_skip_mode_footer(_om_f)
                    and footer_visible_for_user(
                        user_id=str(message.from_user.id) if message.from_user else "",
                        is_admin=bool(
                            _om_f.get("telegram_is_admin")
                            or (
                                message.from_user
                                and self._admin_module.is_admin(str(message.from_user.id))
                            )
                        ),
                    )
                ):
                    _uid_f = str(message.from_user.id) if message.from_user else ""
                    _gid_f = _om_f.get("group_id")
                    if _gid_f is None:
                        _gid_f = str(message.chat.id) if (message.chat and message.chat.type != "private") else None
                    _rec_f = self.orchestrator.behavior_store.load(_uid_f, _gid_f) if _uid_f else {}
                    _fields = build_mode_footer_fields(
                        output_meta=_om_f,
                        plan_module=str(_om_f.get("plan_module") or ""),
                        route_context=_om_f,
                        persisted=_rec_f if isinstance(_rec_f, dict) else None,
                        trace_id=str(_om_f.get("trace_id") or ""),
                    )
                    txt = append_mode_footer(txt, fields=_fields)
                    _om_f["mode_footer_tag"] = _fields.get("machine_tag")
                    output.meta = _om_f
            except Exception as _mf_e:
                logger.debug("reply_mode_footer: %s", _mf_e)
            if not txt:
                uid = str(message.from_user.id) if message.from_user else ""
                admin_tail = (
                    " Админ: /admin_connectivity, /admin_health."
                    if self._admin_module.is_admin(uid)
                    else ""
                )
                txt = "Пустой текст ответа. Повторите запрос или смотрите /admin_logs." + admin_tail
            if _stream_delivered_early is not None:
                if progress_message_id is not None:
                    try:
                        await message.bot.edit_message_text(
                            txt[:4080],
                            chat_id=message.chat.id,
                            message_id=progress_message_id,
                            reply_markup=reply_markup,
                        )
                    except Exception as e:
                        logger.debug("stream progress final edit: %s", e)
                        try:
                            await reply_text_chunks(
                                message,
                                txt,
                                reply_markup=reply_markup,
                                **_reply_to_user_kwargs(message),
                            )
                        except Exception as e2:
                            logger.debug("stream fallback send: %s", e2)
                elif reply_markup is not None:
                    try:
                        await message.bot.edit_message_reply_markup(
                            chat_id=message.chat.id,
                            message_id=progress_message_id,
                            reply_markup=reply_markup,
                        )
                    except Exception as e:
                        logger.debug("stream reply_markup: %s", e)
                return progress_message_id is not None
            if progress_message_id is not None:
                try:
                    await message.bot.edit_message_text(
                        txt[:4080],
                        chat_id=message.chat.id,
                        message_id=progress_message_id,
                        reply_markup=reply_markup,
                    )
                    return True
                except Exception as e:
                    logger.debug("progress_edit_to_answer: %s", e)
            await reply_text_chunks(
                message,
                txt,
                reply_markup=reply_markup,
                **_reply_to_user_kwargs(message),
            )
            return False
        except Exception as e:
            logger.error(f"Error sending output: {e}")
            record_error_event("input_layer", "send_output failed", exc=e)
            await message.answer("Не удалось отправить ответ.", **_reply_to_user_kwargs(message))
            return False

    # ============================================================
    #   START BOT
    # ============================================================
    async def start_polling(self):
        mark_boot("telegram_dispatcher_start_polling")
        logger.info(
            "telegram │ polling │ start",
            extra={"gemma_event": "telegram_poll"},
        )
        await self.dp.start_polling(self.bot)

    async def start_webhook(
        self,
        webhook_url: str,
        webhook_path: str = "/webhook",
        host: str = "0.0.0.0",
        port: int = 8443,
        secret_token: str = "",
    ) -> None:
        mark_boot("telegram_dispatcher_start_webhook")
        logger.info(
            "telegram │ webhook │ start │ url=%s path=%s host=%s port=%s",
            webhook_url, webhook_path, host, port,
            extra={"gemma_event": "telegram_webhook"},
        )

        await self.bot.delete_webhook(drop_pending_updates=True)

        full_url = webhook_url.rstrip("/") + webhook_path
        await self.bot.set_webhook(
            url=full_url,
            secret_token=secret_token or None,
            allowed_updates=[
                "message",
                "edited_message",
                "callback_query",
                "inline_query",
                "chat_member",
                "my_chat_member",
            ],
            max_connections=int(os.getenv("WEBHOOK_MAX_CONNECTIONS", "40")),
            drop_pending_updates=True,
        )
        logger.info("telegram │ webhook set │ url=%s", full_url)

        await self.dp.start_webhook(
            path=webhook_path,
            host=host,
            port=port,
            secret_token=secret_token or None,
        )
