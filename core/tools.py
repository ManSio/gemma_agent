"""
Auto Tools Layer - Автоматический слой инструментов
Берёт все классы *Module во всех модулях core/*
и превращает их публичные методы в инструменты.

Ядро мозга подмешивает в TOOL_CALL служебные поля (например user_id). Чтобы не ловить
TypeError на старых сигнатурах и лишних ключах, kwargs режутся по сигнатуре целевого метода.
"""
from typing import Any, Awaitable, Callable, Dict, FrozenSet
import inspect
import pkgutil
import importlib
import logging
import os

from core.dangerous_command_guard import check_dangerous_tool_call

logger = logging.getLogger(__name__)


def _env_truthy(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def filter_kwargs_for_callable(fn: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Оставить только те именованные аргументы, которые реально принимает fn.
    Если у fn есть **kwargs — возвращаем kwargs без изменений.
    """
    if not _env_truthy("TOOLS_FILTER_KWARGS", default=True):
        return dict(kwargs)
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return dict(kwargs)
    params = list(sig.parameters.values())
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
        return dict(kwargs)
    allowed: set[str] = set()
    for p in params:
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            allowed.add(p.name)
    return {k: v for k, v in kwargs.items() if k in allowed}

ToolFunc = Callable[..., Awaitable[Any]]
TOOLS: Dict[str, ToolFunc] = {}
_tools_scan_done = False
# Префиксы инструментов (напр. "FooBar."), которые *Module явно помечает для BRAIN_TOOLS_MODE=auto|lite
_BRAIN_LITE_OPT_IN_PREFIXES: set[str] = set()


def _register_tool(name: str, func: ToolFunc):
    if name in TOOLS:
        return
    TOOLS[name] = func
    logger.debug("[tools] registered tool: %s", name)


def _ensure_tools_scan() -> None:
    """Первый запрос к инструментам — тогда сканируем core/* (тяжёлый импорт)."""
    global _tools_scan_done
    if _tools_scan_done:
        return
    _auto_discover_tools()
    _tools_scan_done = True


async def run_tool(tool_name: str, /, **kwargs) -> Any:
    """Выполнить инструмент по имени.

    tool_name — только позиционный (`/`): иначе kwargs с ключом name от LLM/инструментов
    конфликтует с именем параметра и даёт TypeError: multiple values for argument 'name'.
    """
    _ensure_tools_scan()
    func = TOOLS.get(tool_name)
    if not func:
        return {"error": f"unknown tool: {tool_name}"}

    # ── Dangerous Command Guard ──
    guard_result = check_dangerous_tool_call(tool_name, kwargs)
    if guard_result is not None:
        logger.warning("[tools] DANGEROUS COMMAND GUARD blocked %s", tool_name)
        return {
            "error": f"dangerous command guard: {guard_result.get('reason')}",
            "tool": tool_name,
            "guard_blocked": True,
        }

    try:
        return await func(**kwargs)
    except Exception as e:
        logger.exception(f"[tools] tool {tool_name} failed: {e}")
        return {"error": str(e), "tool": tool_name}


def list_tools() -> Dict[str, str]:
    """Список доступных инструментов (имена)"""
    _ensure_tools_scan()
    return {name: "auto-discovered" for name in TOOLS.keys()}


def brain_lite_opt_in_prefixes() -> FrozenSet[str]:
    """Префиксы с точкой, разрешённые в auto/lite помимо базового списка в agent.py (см. BRAIN_LITE_INCLUDE на *Module)."""
    _ensure_tools_scan()
    return frozenset(_BRAIN_LITE_OPT_IN_PREFIXES)


def _auto_discover_tools():
    """Автоматически находит все методы классов *Module в core/* и регистрирует их как инструменты"""
    try:
        import core
    except ImportError:
        logger.error("[tools] cannot import core package")
        return

    for module_info in pkgutil.walk_packages(core.__path__, core.__name__ + "."):
        mod_name = module_info.name
        try:
            module = importlib.import_module(mod_name)
        except Exception as e:
            logger.warning(f"[tools] skip module {mod_name}: {e}")
            continue

        for cls_name, cls in inspect.getmembers(module, inspect.isclass):
            # Берём только классы, заканчивающиеся на Module
            if not cls_name.endswith("Module"):
                continue
            # Требуют DI из оркестратора / input_layer — не для нулевого конструктора
            if cls_name in {"UserManagementModule", "SelfConfigModule"}:
                continue

            # Пытаемся создать инстанс без аргументов
            try:
                instance = cls()
            except Exception as e:
                logger.warning(f"[tools] cannot instantiate {cls_name} from {mod_name}: {e}")
                continue

            prefix = cls_name[:-6] or cls_name  # убираем 'Module'
            if bool(getattr(cls, "BRAIN_LITE_INCLUDE", False)):
                _BRAIN_LITE_OPT_IN_PREFIXES.add(prefix + ".")

            for meth_name, meth in inspect.getmembers(instance, callable):
                if meth_name.startswith("_"):
                    continue

                tool_name = f"{prefix}.{meth_name}"

                async def _wrapper(*args, _m=meth, **kwargs):
                    call_kw = filter_kwargs_for_callable(_m, kwargs)
                    result = _m(*args, **call_kw)
                    if inspect.isawaitable(result):
                        return await result  # type: ignore[misc]
                    return result

                _register_tool(tool_name, _wrapper)

    logger.info("[tools] auto-discovered %s tools", len(TOOLS))
