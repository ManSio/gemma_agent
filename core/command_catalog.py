"""
Единый runtime-каталог команд бота — Single Source of Truth.

Зачем:
- inline_slash_dispatch, slash_exclusive, /help и admin-отчёты раньше держали
  собственные списки команд. Любое расхождение приводило к "тихим" багам:
  команда есть в одном месте и нет в другом, exclusive без runner, ручные
  индексы кнопок съезжают и т.п.
- Теперь все потребители читают этот каталог. Чтобы добавить core-команду,
  нужно только дописать запись в CORE_COMMANDS — UI/dispatch/skip
  синхронизируются автоматически.
- Команды плагинов берутся из manifest.commands и не дублируются вручную.

Контракт (CoreCommandSpec):
- token: имя команды без `/`, lowercase (например "admin_health")
- runner_attr: имя async-функции в core.input_handlers.telegram_command_runners
  (если None — команда обрабатывается только через aiogram Command(...) хендлер
  в commands_*.py и НЕ может быть запущена через synthetic-inline payload).
- aliases: альтернативные написания (например "status" -> "system_state")
- exclusive: True — оркестратор пропускает (команду полностью обслуживает ядро)
- visibility: "public" | "admin" — для help/UI и фильтрации
- label: короткая подпись для кнопок (если пусто — токен будет показан как есть)
- group: семантическая группа (для /help группировки)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Literal, Optional, Set, Tuple

Visibility = Literal["public", "admin"]


@dataclass(frozen=True)
class CoreCommandSpec:
    token: str
    runner_attr: Optional[str] = None
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    exclusive: bool = True
    visibility: Visibility = "public"
    label: str = ""
    group: str = "general"

    def all_tokens(self) -> Tuple[str, ...]:
        return (self.token,) + tuple(self.aliases)


# ВАЖНО: token и aliases должны быть в нижнем регистре, без слеша.
# Любая команда, которую ядро обслуживает само и оркестратор должен
# пропускать, должна быть здесь.
CORE_COMMANDS: Tuple[CoreCommandSpec, ...] = (
    # --- Базовые публичные команды (есть runner для synthetic inline) ---
    CoreCommandSpec("start", "run_start", label="Старт", group="basic"),
    CoreCommandSpec("help", "run_help", label="Справка", group="basic"),
    CoreCommandSpec("plugins", "run_plugins", label="Плагины", group="basic"),
    CoreCommandSpec("plugins_help", "run_plugins_help", label="Справка по плагинам", group="basic"),
    CoreCommandSpec(
        "system_state",
        "run_system_state",
        aliases=("status",),
        label="Состояние",
        group="basic",
    ),
    CoreCommandSpec("get_mem0_facts", "run_get_mem0_facts", label="Факты Mem0", group="basic"),
    CoreCommandSpec(
        "geo_help",
        "run_geo_help",
        label="Карты и геолокация",
        group="basic",
    ),
    CoreCommandSpec("id", "run_id", label="Мой ID", group="basic"),
    CoreCommandSpec("me", "run_me", label="Профиль", group="profile"),
    CoreCommandSpec("psych", "run_psych", label="Психопрофиль", group="profile"),
    CoreCommandSpec("twin", "run_twin", label="Двойник", group="profile"),
    CoreCommandSpec(
        "chat_style",
        "run_chat_style",
        aliases=("style", "стиль"),
        label="Стиль чата",
        group="profile",
    ),
    CoreCommandSpec("facts", "run_facts", label="Факты", group="profile"),
    CoreCommandSpec("forget", "run_forget", label="Забыть факт", group="profile"),
    CoreCommandSpec("facts_refresh", "run_facts_refresh", label="Обновить факты", group="profile"),
    CoreCommandSpec("facts_reset", "run_facts_reset", label="Сбросить факты", group="profile"),
    CoreCommandSpec(
        "new",
        "run_new_conversation",
        label="Новый диалог (эпоха)",
        group="profile",
    ),
    CoreCommandSpec("filefrom", "run_filefrom", label="Файл от...", group="basic"),
    CoreCommandSpec(
        "corpus_doc",
        "run_corpus_doc",
        label="Файл из корпуса",
        group="basic",
    ),
    CoreCommandSpec(
        "corpus_books",
        "run_corpus_books",
        label="Книги в корпусе",
        group="basic",
    ),
    CoreCommandSpec(
        "corpus_docs",
        "run_corpus_docs",
        label="Документы в корпусе",
        group="basic",
    ),
    CoreCommandSpec(
        "corpus_file",
        "run_corpus_file",
        label="Оригинал из корпуса",
        group="basic",
    ),
    CoreCommandSpec(
        "corpus_delete",
        "run_corpus_delete",
        label="Удалить из корпуса",
        group="basic",
    ),
    CoreCommandSpec(
        "calc",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Калькулятор",
        group="basic",
    ),
    CoreCommandSpec(
        "goal_run",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Многошаговая цель",
        group="goals",
    ),
    CoreCommandSpec(
        "goal_step",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Шаг цели",
        group="goals",
    ),
    CoreCommandSpec(
        "goal_status",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Статус цели",
        group="goals",
    ),
    CoreCommandSpec(
        "goal_cancel",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Сброс цели",
        group="goals",
    ),
    CoreCommandSpec(
        "weather",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Погода",
        group="tools",
    ),
    CoreCommandSpec(
        "wiki",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Википедия",
        group="tools",
    ),
    CoreCommandSpec(
        "search",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Поиск",
        group="tools",
    ),
    CoreCommandSpec(
        "remind",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Напоминание",
        group="tools",
    ),
    CoreCommandSpec(
        "translate",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Перевод",
        group="tools",
    ),
    CoreCommandSpec(
        "summarize",
        runner_attr=None,
        exclusive=False,
        visibility="public",
        label="Пересказ",
        group="tools",
    ),
    CoreCommandSpec(
        "note",
        runner_attr="run_note",
        exclusive=False,
        visibility="public",
        label="Заметка",
        group="tools",
    ),
    CoreCommandSpec(
        "rate",
        runner_attr="run_rate",
        exclusive=True,
        visibility="public",
        label="Оценить ответ (+1/-1)",
        group="profile",
    ),
    CoreCommandSpec(
        "correct",
        runner_attr="run_correct",
        exclusive=True,
        visibility="public",
        label="Поправить ответ бота",
        group="profile",
    ),
    # --- Латки (admin-only по факту, но синтаксически exclusive) ---
    CoreCommandSpec(
        "remember_patch", "run_remember_patch", visibility="admin", label="Запомнить латку", group="patches"
    ),
    CoreCommandSpec(
        "forget_patch", "run_forget_patch", visibility="admin", label="Забыть латку", group="patches"
    ),
    CoreCommandSpec(
        "clear_all_patches", "run_clear_all_patches", visibility="admin", label="Сброс латок", group="patches"
    ),
    CoreCommandSpec(
        "export_patches", "run_export_patches", visibility="admin", label="Экспорт латок", group="patches"
    ),
    CoreCommandSpec(
        "list_patches", "run_list_patches", visibility="admin", label="Список латок", group="patches"
    ),
    CoreCommandSpec(
        "pending_suggested_patch",
        "run_pending_suggested_patch",
        visibility="admin",
        label="Очередь латок",
        group="patches",
    ),
    # approve/dismiss обрабатываются только aiogram-хендлером в commands_admin.py
    # (нет inline-runner — synthetic payload их не должен синтезировать).
    CoreCommandSpec(
        "approve_suggested_patch",
        runner_attr=None,
        visibility="admin",
        label="Одобрить латку",
        group="patches",
    ),
    CoreCommandSpec(
        "dismiss_suggested_patch",
        runner_attr=None,
        visibility="admin",
        label="Отклонить латку",
        group="patches",
    ),
    CoreCommandSpec(
        "admin_git",
        runner_attr=None,
        visibility="admin",
        label="Git push",
        group="net",
    ),
)


# ---------------------------------------------------------------------------
# Базовые помощники для нормализации текста команды.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"^/(?P<tok>[^\s@/]+)", re.UNICODE)


def normalize_command_token(text: str) -> str:
    """Возвращает токен slash-команды (без слеша, lowercase) или ''."""
    if not text:
        return ""
    raw = text.strip()
    if not raw.startswith("/"):
        return ""
    m = _TOKEN_RE.match(raw)
    if not m:
        return ""
    return m.group("tok").lower()


def is_admin_command_pattern(token: str) -> bool:
    """Все admin_* и auto_* — admin-only по умолчанию (исключения через CORE_COMMANDS)."""
    if not token:
        return False
    return token.startswith("admin") or token.startswith("auto_")


# ---------------------------------------------------------------------------
# Производные структуры (генерируются один раз).
# ---------------------------------------------------------------------------

def _build_runner_attr_map() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for spec in CORE_COMMANDS:
        if not spec.runner_attr:
            continue
        for tok in spec.all_tokens():
            out[tok] = spec.runner_attr
    return out


def _build_exclusive_tokens() -> FrozenSet[str]:
    out: set[str] = set()
    for spec in CORE_COMMANDS:
        if not spec.exclusive:
            continue
        for tok in spec.all_tokens():
            out.add(tok)
    return frozenset(out)


_RUNNER_ATTR_MAP: Dict[str, str] = _build_runner_attr_map()
_EXCLUSIVE_TOKENS: FrozenSet[str] = _build_exclusive_tokens()


def get_core_runner_attrs() -> Dict[str, str]:
    """Map: токен -> имя runner-функции в telegram_command_runners."""
    return _build_runner_attr_map()


def get_core_exclusive_tokens() -> FrozenSet[str]:
    """Множество токенов, которые ядро обрабатывает само (оркестратор пропускает)."""
    return _build_exclusive_tokens()


def is_core_exclusive_token(token: str) -> bool:
    if not token:
        return False
    if token in _EXCLUSIVE_TOKENS:
        return True
    return is_admin_command_pattern(token)


def is_orchestrator_skip_text(text: str) -> bool:
    """Должен ли оркестратор пропустить это сообщение (ядро уже обработает)."""
    return is_core_exclusive_token(normalize_command_token(text))


def find_core_spec(token: str) -> Optional[CoreCommandSpec]:
    if not token:
        return None
    for spec in CORE_COMMANDS:
        if token == spec.token or token in spec.aliases:
            return spec
    return None


# ---------------------------------------------------------------------------
# Plugin commands (manifest-driven).
# ---------------------------------------------------------------------------

def iter_plugin_command_tokens(plugin_registry: Any) -> Dict[str, List[str]]:
    """
    Возвращает {plugin_name: [tokens]} только для загруженных плагинов.
    Тихо пропускает мусорные манифесты.
    """
    out: Dict[str, List[str]] = {}
    loaded = getattr(plugin_registry, "loaded_modules", {}) or {}
    for name, mod in loaded.items():
        manifest = getattr(mod, "manifest", None)
        if not manifest or not hasattr(manifest, "iter_command_tokens"):
            continue
        try:
            tokens = manifest.iter_command_tokens() or []
        except Exception:
            tokens = []
        clean: List[str] = []
        for t in tokens:
            tok = (str(t) or "").strip().lstrip("/").split("@")[0].lower()
            if tok:
                clean.append(tok)
        if clean:
            out[str(name)] = clean
    return out


def find_plugin_for_command(plugin_registry: Any, token: str) -> Optional[str]:
    """Возвращает имя загруженного плагина, у которого в манифесте есть этот token."""
    if not token:
        return None
    for plugin_name, tokens in iter_plugin_command_tokens(plugin_registry).items():
        if token in tokens:
            return plugin_name
    return None


def find_command_collisions(plugin_registry: Any) -> Dict[str, List[str]]:
    """
    Возвращает токены, которые объявлены сразу в нескольких источниках
    (CORE + плагин или несколько плагинов). Для админ-диагностики.
    """
    counts: Dict[str, List[str]] = {}
    for spec in CORE_COMMANDS:
        for tok in spec.all_tokens():
            counts.setdefault(tok, []).append("core")
    for plugin_name, tokens in iter_plugin_command_tokens(plugin_registry).items():
        for tok in tokens:
            counts.setdefault(tok, []).append(f"plugin:{plugin_name}")
    return {tok: owners for tok, owners in counts.items() if len(owners) > 1}


def collect_full_catalog(plugin_registry: Any) -> Dict[str, Any]:
    """Развёрнутый снимок каталога — для admin-отчётов и тестов."""
    core_rows: List[Dict[str, Any]] = []
    for spec in CORE_COMMANDS:
        core_rows.append(
            {
                "token": spec.token,
                "aliases": list(spec.aliases),
                "runner_attr": spec.runner_attr or "",
                "exclusive": bool(spec.exclusive),
                "visibility": spec.visibility,
                "label": spec.label,
                "group": spec.group,
            }
        )
    plugin_rows: List[Dict[str, Any]] = []
    for plugin_name, tokens in iter_plugin_command_tokens(plugin_registry).items():
        plugin_rows.append({"plugin": plugin_name, "commands": tokens})
    return {
        "core_total": len(core_rows),
        "plugins_total": len(plugin_rows),
        "core_commands": core_rows,
        "plugin_commands": plugin_rows,
        "collisions": find_command_collisions(plugin_registry),
    }


_HANDLER_COMMAND_RE = re.compile(r'Command\(\s*"([^"]+)"')


def discover_aiogram_command_tokens() -> Set[str]:
    """
    Все токены из @dp.message(Command("…")) в core/input_handlers/commands_*.py.
    Прогоняйте тест test_discovered_commands_match_files при добавлении хендлера.
    """
    base_dir = Path(__file__).resolve().parent
    out: set[str] = set()
    for path in sorted(base_dir.glob("input_handlers/commands_*.py")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _HANDLER_COMMAND_RE.finditer(raw):
            tok = (m.group(1) or "").strip().lower()
            if tok:
                out.add(tok)
    return out


def all_slash_tokens_for_brain_catalog() -> List[str]:
    """Объединение: хендлеры aiogram + все токены/алиасы из CORE_COMMANDS (calc, goal_*, алиасы)."""
    bag: set[str] = set(discover_aiogram_command_tokens())
    for spec in CORE_COMMANDS:
        for t in spec.all_tokens():
            bag.add(str(t).strip().lower())
    return sorted(bag)


BRAIN_COMMAND_CATALOG_USAGE = """
Как пользоваться: полные описания — /help (разделы «Вы», «Админ», «Плагины»).
Коротко: /status — состояние; /filefrom URL — файл; /calc выражение — калькулятор;
/goal_run /goal_step /goal_status /goal_cancel — многошаговая цель при GOAL_RUNNER_ENABLED; ZIP диагностики — /zip_read bundle.json (плагин tools);
учёба — /explain /solve /check /quiz; персона — /personas; Mem0 — /get_mem0_facts.
Не выдумывай slash-команд вне этого списка и манифестов плагинов.
""".strip()

# Сокращённый набор для обычного чата (без простыни всех admin_*). Порядок — для читаемости.
_MINIMAL_SLASH_TOKENS_ORDER: Tuple[str, ...] = (
    "start",
    "help",
    "status",
    "system_state",
    "me",
    "id",
    "chat_style",
    "style",
    "facts",
    "forget",
    "rate",
    "correct",
    "facts_refresh",
    "facts_reset",
    "get_mem0_facts",
    "filefrom",
    "corpus_books",
    "corpus_docs",
    "corpus_doc",
    "corpus_file",
    "corpus_delete",
    "imagine",
    "calc",
    "plugins",
    "plugins_help",
    "goal_run",
    "goal_step",
    "goal_status",
    "goal_cancel",
    "personas",
    "get_persona",
    "set_persona",
    "list_personas",
    "explain",
    "solve",
    "check",
    "quiz",
    "zip_read",
    "read_file",
    "zip_list",
    "zip_pack",
    "save_file",
    "parse",
)


def format_brain_telegram_command_catalog(
    plugin_registry: Any,
    *,
    tier: str = "full",
    max_chars: int = 14_000,
    max_module_commands: int = 160,
) -> str:
    """
    Текст блока telegram_commands_catalog для мозга.
    tier:
      - minimal — частые пользовательские + утилиты; без полного списка admin_*.
      - full — все слэши ядра + команды плагинов (для /help, админа, явного запроса).
    plugin_registry может быть None (только ядро).
    """
    tier = (tier or "full").strip().lower()
    if tier == "minimal":
        return _format_brain_command_catalog_minimal(plugin_registry, max_chars=max_chars)
    return _format_brain_command_catalog_full(
        plugin_registry, max_chars=max_chars, max_module_commands=max_module_commands
    )


def _format_brain_command_catalog_minimal(plugin_registry: Any, *, max_chars: int = 3200) -> str:
    allowed = set(all_slash_tokens_for_brain_catalog())
    lines: List[str] = [
        "Slash-команды (сокращённый список для обычного чата; полный каталог — по запросу «команды», /help, для админа):",
        "",
        BRAIN_COMMAND_CATALOG_USAGE,
        "",
        "Частые команды:",
    ]
    for tok in _MINIMAL_SLASH_TOKENS_ORDER:
        if tok in allowed:
            lines.append(f"  /{tok}")
    lines.extend(
        [
            "",
            "Администрирование: полный перечень /admin_* — только у админов; в чате скажи «открой /help → Админ» или выполни /admin.",
        ]
    )
    try:
        from core.input_handlers.help_payload import collect_command_catalog as _plugin_cmds

        rows = _plugin_cmds(plugin_registry) if plugin_registry is not None else []
    except Exception:
        rows = []
    if rows:
        lines.extend(["", "Плагины (только частые триггеры из minimal, остальное — /plugins и /help → Плагины):"])
        want = {t.lstrip("/") for t in _MINIMAL_SLASH_TOKENS_ORDER}
        n = 0
        for r in rows:
            trig = (r.get("trigger") or "").strip().lstrip("/").split("@")[0].lower()
            if trig not in want:
                continue
            mod = (r.get("module") or "").strip()
            desc = (r.get("description") or "").strip()
            piece = f"  /{trig}"
            if mod:
                piece += f" → {mod}"
            if desc:
                piece += f" — {desc}"
            lines.append(piece)
            n += 1
            if n >= 24:
                break

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 32)] + "\n… (minimal каталог обрезан)"
    return text


def _format_brain_command_catalog_full(
    plugin_registry: Any,
    *,
    max_chars: int = 14_000,
    max_module_commands: int = 160,
) -> str:
    lines: List[str] = [
        "Канонические slash-команды (имена не придумывать — только этот список и манифесты):",
        "",
        BRAIN_COMMAND_CATALOG_USAGE,
        "",
        "Ядро (handlers + объявления CORE_COMMANDS, по одной на строке):",
    ]
    for tok in all_slash_tokens_for_brain_catalog():
        lines.append(f"  /{tok}")

    lines.extend(["", "Плагины (manifest commands, загруженные модули):"])
    try:
        from core.input_handlers.help_payload import collect_command_catalog as _plugin_cmds

        rows = _plugin_cmds(plugin_registry) if plugin_registry is not None else []
    except Exception:
        rows = []
    if not rows:
        lines.append("  (нет загруженных команд плагинов или реестр недоступен)")
    else:
        for r in rows[:max_module_commands]:
            trig = (r.get("trigger") or "").strip()
            mod = (r.get("module") or "").strip()
            desc = (r.get("description") or "").strip()
            piece = f"  {trig}" if trig.startswith("/") else f"  /{trig.lstrip('/')}"
            if mod:
                piece += f" → {mod}"
            if desc:
                piece += f" — {desc}"
            lines.append(piece)
        if len(rows) > max_module_commands:
            lines.append(f"  … ещё команд плагинов: {len(rows) - max_module_commands}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 32)] + "\n… (каталог обрезан по лимиту)"
    return text


__all__ = [
    "CoreCommandSpec",
    "CORE_COMMANDS",
    "normalize_command_token",
    "is_admin_command_pattern",
    "is_core_exclusive_token",
    "is_orchestrator_skip_text",
    "get_core_runner_attrs",
    "get_core_exclusive_tokens",
    "find_core_spec",
    "iter_plugin_command_tokens",
    "find_plugin_for_command",
    "find_command_collisions",
    "collect_full_catalog",
    "discover_aiogram_command_tokens",
    "all_slash_tokens_for_brain_catalog",
    "format_brain_telegram_command_catalog",
    "BRAIN_COMMAND_CATALOG_USAGE",
]
