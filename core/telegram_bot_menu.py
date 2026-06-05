"""
Меню slash-команд в Telegram (иконка списка слева от поля ввода) — Bot API setMyCommands.

Список собирается при старте: публичные команды ядра + /admin + команды загруженных плагинов (до лимита API).
"""
from __future__ import annotations

import logging
import os
from typing import Any, List, Set

import re

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

from core.command_catalog import CORE_COMMANDS, CoreCommandSpec

logger = logging.getLogger(__name__)

_TELEGRAM_MENU_MAX = 100
# Bot API: команда в меню — латиница в нижнем регистре, цифры, подчёркивание, длина 1–32.
_CMD_OK = re.compile(r"^[a-z0-9_]{1,32}$")


def _is_valid_telegram_command(cmd: str) -> bool:
    return cmd.startswith("/") and " " not in cmd and "<" not in cmd and ">" not in cmd


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clip_desc(text: str, max_len: int = 256) -> str:
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


# Подписи в меню Telegram (setMyCommands) для плагинов, у которых в manifest только EN.
# Плюс в module.json можно задать description_ru / menu_ru — они имеют приоритет.
_PLUGIN_MENU_DESCRIPTION_RU: dict[str, str] = {
    "ambiguity_detect": "Флаги неоднозначности формулировки задачи",
    "attn_bench": "Встроенный бенчмарк внимательности",
    "attn_count": "Подсчёт символов с детерминированными фильтрами",
    "benchmark_run": "Бенчмарк: quick | full | nightly, наборы suite",
    "causal_check": "Проверка согласованности причинно-следственной топологии",
    "conflict_extract": "Противоречия в парах «субъект — значение»",
    "consistency_guard": "Согласованность ответа с ограничениями must/forbid",
    "context_reduce": "Сжатие длинного контекста",
    "context_stability": "Дрейф условий между промежуточными шагами",
    "epistemic_audit": "Классификация эпистемического статуса утверждения",
    "error_memory": "Учёт и проверка повторяющихся ошибок",
    "fsm_inspect": "Разбор FSM: недостижимость состояний и достижимость путей",
    "hidden_vars": "Переменные только в ответе, не в ограничениях",
    "instruction_track": "Покрытие ответа шагами инструкции",
    "local_cache": "Локальный кэш: получить / записать / удалить",
    "local_diff": "Unified diff локально",
    "local_fs": "Безопасное чтение и запись локального файла",
    "local_math": "Локальная арифметика",
    "local_parse": "Разбор структурированного текста локально",
    "local_regex": "Поиск по regex локально",
    "local_text": "Локальные текстовые операции",
    "local_tokenize": "Подсчёт символов, слов и токенов",
    "meta_reason": "Выбор пакета стратегий рассуждения под задачу",
    "minimal_model": "Минимальная модель из must/forbid и отношений",
    "propagate_constraints": "Проверка шагов с распространением ограничений",
    "qei_check": "Оценки QEI / Ford–Roman для утверждений об энергии",
    "reason_bench": "Интегр. бенч внимательности с вердиктом",
    "reason_check": "Детерминированный конвейер для символьных задач",
    "reason_conflicts": "ConflictExtractor через обёртку вердикта",
    "reason_consistency": "ConsistencyGuard через обёртку вердикта",
    "reason_fsm": "StateMachineInspector через обёртку вердикта",
    "reason_hidden": "HiddenVariableFinder через обёртку вердикта",
    "reason_model": "MinimalModelBuilder через обёртку вердикта",
    "reason_timeline": "ContextTimeline через обёртку вердикта",
    "reason_unicode": "UnicodeNormalizer через обёртку вердикта",
    "self_check": "Сверка ответа модели с детерминированным эталоном",
    "solution_explorer": "Несколько альтернативных путей со структурным скорингом",
    "symbol_count": "Точный подсчёт вхождений символов",
    "task_classify": "Классификация задачи для выбора стратегии",
    "text_filter": "Детерминированные фильтры текста",
    "timeline_build": "Временная шкала и временные противоречия",
    "topology_check": "Проверка устойчивости топологии",
    "unicode_normalize": "Нормализация Unicode (NFC/NFKC) и варианты",
    "verdict": "Строгий вердикт из JSON на входе",
}


def _plugin_menu_description_ru(tok: str, desc: str) -> str:
    t = (tok or "").strip().lower()
    if not t:
        return _clip_desc(desc, 256)
    ru = _PLUGIN_MENU_DESCRIPTION_RU.get(t)
    if ru:
        return _clip_desc(ru, 256)
    return _clip_desc(desc, 256)


def _menu_description(spec: CoreCommandSpec) -> str:
    base = (spec.label or "").strip() or spec.token.replace("_", " ")
    return _clip_desc(base, 256)


def _plugin_menu_entries(plugin_registry: Any) -> List[tuple[str, str]]:
    out: List[tuple[str, str]] = []
    loaded = getattr(plugin_registry, "loaded_modules", {}) or {}
    for _name, mod in loaded.items():
        manifest = getattr(mod, "manifest", None)
        if not manifest:
            continue
        raw = getattr(manifest, "commands", None) or []
        for c in raw:
            tok = ""
            desc = ""
            desc_ru = ""
            if isinstance(c, str):
                tok = c.strip().lstrip("/").split("@")[0].split(maxsplit=1)[0].lower()
            elif isinstance(c, dict):
                trig = str(c.get("trigger") or c.get("command") or c.get("name") or "").strip()
                tok = trig.lstrip("/").split("@")[0].split(maxsplit=1)[0].lower()
                desc_ru = str(c.get("description_ru") or c.get("menu_ru") or "").strip()
                desc = str(c.get("description") or "").strip()
            if not tok:
                continue
            if isinstance(c, dict) and desc_ru:
                out.append((tok, _clip_desc(desc_ru, 256)))
                continue
            if not desc:
                desc = tok.replace("_", " ")
            out.append((tok, _plugin_menu_description_ru(tok, desc)))
    return out


def build_bot_menu_commands(plugin_registry: Any) -> List[BotCommand]:
    """Порядок: базовые → /admin → остальные core public → плагины (без дубликатов)."""
    seen: Set[str] = set()
    ordered: List[BotCommand] = []

    def add(cmd: str, description: str) -> None:
        c = (cmd or "").strip().lower()
        if not c or c in seen:
            return
        if not _CMD_OK.match(c):
            logger.warning("telegram menu: пропуск команды %r (недопустимо для setMyCommands)", c)
            return
        seen.add(c)
        d = (description or "").strip() or c.replace("_", " ")
        ordered.append(BotCommand(command=c, description=_clip_desc(d, 256)))

    priority_public = (
        "start",
        "help",
        "geo_help",
        "admin",
        "plugins",
        "plugins_help",
        "system_state",
        "get_mem0_facts",
        "id",
        "me",
        "psych",
        "twin",
        "chat_style",
        "facts",
        "forget",
        "facts_refresh",
        "facts_reset",
        "filefrom",
        "corpus_doc",
        "corpus_books",
        "corpus_docs",
    )
    public_specs = [s for s in CORE_COMMANDS if s.visibility == "public"]
    by_token = {s.token: s for s in public_specs}

    for tok in priority_public:
        if tok == "admin":
            add("admin", "Панель администратора")
            continue
        spec = by_token.get(tok)
        if spec:
            add(spec.token, _menu_description(spec))

    for spec in public_specs:
        add(spec.token, _menu_description(spec))

    for tok, desc in _plugin_menu_entries(plugin_registry):
        add(tok, desc)
        if len(ordered) >= _TELEGRAM_MENU_MAX:
            break

    return ordered[:_TELEGRAM_MENU_MAX]


async def sync_telegram_bot_menu(bot: Bot, plugin_registry: Any) -> None:
    if _env_truthy("TELEGRAM_BOT_MENU_OFF", False):
        logger.info("telegram menu: skip (TELEGRAM_BOT_MENU_OFF)")
        return
    try:
        cmds = build_bot_menu_commands(plugin_registry)
        if not cmds:
            logger.warning("telegram menu: пустой список команд, setMyCommands не вызываем")
            return
        valid_commands = [c for c in cmds if _is_valid_telegram_command(f"/{c.command}")]
        # Два scope: иначе в части клиентов список у «три полоски» в личке не подхватывается.
        await bot.set_my_commands(valid_commands, BotCommandScopeDefault())
        await bot.set_my_commands(valid_commands, BotCommandScopeAllPrivateChats())
        preview = ", ".join(c.command for c in valid_commands[:12])
        logger.info(
            "telegram menu: setMyCommands ×2 (default + all_private_chats), count=%s, head=%s%s",
            len(valid_commands),
            preview,
            "…" if len(valid_commands) > 12 else "",
        )
    except Exception as e:
        logger.error("telegram menu: setMyCommands failed: %s", e, exc_info=True)
