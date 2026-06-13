"""
Plugin Registry для управления модулями
"""
import os
import json
import logging
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Union
from pathlib import Path
from pydantic import BaseModel, Field
from datetime import datetime
from core.models import ModuleState, SystemState
from core.event_bus import bus
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

# Локальный кэш импортированных классов: module_name -> class
_lazy_import_cache: Dict[str, Any] = {}
_hot_install_lock = threading.Lock()


class ModuleManifest(BaseModel):
    """Манифест модуля из module.json"""
    name: str
    version: str
    type: str  # tool, model, rag, orchestrator, ui, input
    entrypoint: str  # путь к классу модуля
    description: str = ""
    input_types: List[str] = Field(default_factory=list)
    output_types: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    prompts: Dict[str, str] = Field(
        default_factory=dict,
        description="Тексты для LLM: подмешиваются в call_brain как plugin_manifest_prompts (только загруженные модули).",
    )
    commands: List[Any] = Field(default_factory=list)
    buttons: List[Dict[str, Any]] = Field(default_factory=list)
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    requires: List[str] = Field(
        default_factory=list,
        description="Имена core-библиотек/флагов (см. module_loader / library_loader)",
    )
    pip_requirements: List[str] = Field(
        default_factory=list,
        description="Строки pip, например httpx>=0.27 — для Docker: scripts/merge_plugin_requirements.py",
    )
    bundled_with: str = Field(
        default="",
        description="Версия релиза приложения (файл VERSION в корне); проставляется scripts/sync_versions.py",
    )
    gemma_tier: str = Field(
        default="",
        description="Класс модуля A|B|C|D|DEV — config/modules_catalog.json",
    )
    gemma_evidence: str = Field(
        default="",
        description="Как проверять: pytest, release_guard, slash, dormant",
    )

    def iter_command_tokens(self) -> List[str]:
        """Normalized command names (no leading '/', lowercase) for slash routing."""
        tokens: List[str] = []
        for c in self.commands or []:
            if isinstance(c, str):
                t = c.strip().lstrip("/").split("@")[0].lower()
                if t:
                    tokens.append(t)
            elif isinstance(c, dict):
                for key in ("trigger", "command", "name"):
                    val = c.get(key)
                    if isinstance(val, str) and val.strip():
                        t = val.strip().lstrip("/").split("@")[0].lower()
                        if t:
                            tokens.append(t)
                            break
        return tokens


class ModuleInstance:
    """Инстанс загруженного модуля. Класс импортируется лениво — при первом enable_module()."""
    def __init__(self, name: str, manifest: ModuleManifest, module_class: Any = None):
        self.name = name
        self.manifest = manifest
        self._entrypoint = manifest.entrypoint
        self._module_class = module_class  # None при ленивой загрузке
        self.instance = None
        self.state = ModuleState(
            name=name,
            type=manifest.type,
            status="disabled"
        )
        self.config = {}

    @property
    def module_class(self) -> Any:
        """Ленивый импорт класса модуля при первом обращении."""
        if self._module_class is not None:
            return self._module_class
        ep = self._entrypoint
        cache_key = ep
        if cache_key in _lazy_import_cache:
            self._module_class = _lazy_import_cache[cache_key]
            return self._module_class
        try:
            module_module, module_class_name = ep.rsplit(':', 1)
            cls = getattr(__import__(module_module, fromlist=[module_class_name]), module_class_name)
            _lazy_import_cache[cache_key] = cls
            self._module_class = cls
            logger.debug("lazy import: %s -> %s", self.name, ep)
            return cls
        except Exception as e:
            logger.error("lazy import failed for %s (%s): %s", self.name, ep, e)
            raise

    def load(self, config: Dict[str, Any] = None):
        """Загрузить инстанс модуля"""
        try:
            self.instance = self.module_class()
            self.config = config or {}
            self.state.status = "healthy"
            self.state.last_check = datetime.now()
            logger.info(
                "plugin │ instance │ %s │ ok",
                self.name,
                extra={"gemma_event": "plugin_instance", "plugin": self.name},
            )
        except Exception as e:
            self.state.status = "failed"
            self.state.last_error = str(e)
            logger.error(
                "plugin │ instance │ %s │ %s",
                self.name,
                e,
                extra={"gemma_event": "plugin_instance_fail", "plugin": self.name},
            )

    def unload(self):
        """Выгрузить модуль"""
        self.instance = None
        self.state.status = "disabled"
        self.state.last_check = datetime.now()
        logger.info(f"Module {self.name} unloaded")


class PluginRegistry:
    """Реестр плагинов для управления модулями"""

    def __init__(self, modules_path: str = "./modules"):
        self.modules_path = Path(modules_path)
        self.modules: Dict[str, ModuleInstance] = {}
        self.loaded_modules: Dict[str, ModuleInstance] = {}
        self.policy_engine = None

    # ==========================
    #   LOAD ALL MODULES
    # ==========================
    def load_all_modules(self):
        """Загрузить и ВКЛЮЧИТЬ все модули"""
        if not self.modules_path.exists():
            logger.warning(f"Modules directory {self.modules_path} does not exist")
            return

        for module_dir in self.modules_path.iterdir():
            if module_dir.is_dir():
                module = self.load_module(module_dir)
                if module:
                    self.enable_module(module.name)

    # ==========================
    #   LOAD SINGLE MODULE
    # ==========================
    def load_module(self, module_path: Path) -> Optional[ModuleInstance]:
        """Загрузить один модуль"""
        try:
            manifest_path = module_path / "module.json"
            if not manifest_path.exists():
                logger.warning(
                    "plugin │ skip │ %s │ no module.json",
                    module_path.name,
                    extra={"gemma_event": "plugin_skip", "plugin": module_path.name},
                )
                return None

            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest_data = json.load(f)

            manifest = ModuleManifest(**manifest_data)
            if manifest.pip_requirements:
                logger.info(
                    "Module %s declares pip_requirements %s — рантайм не ставит пакеты; "
                    "пересоберите образ (merge_plugin_requirements.py).",
                    manifest.name,
                    manifest.pip_requirements,
                )

            # Валидация плагинного контракта (warnings — в лог, errors — тоже не блокируют,
            # но видны через /admin_plugins_health и release-guard).
            try:
                from core.plugin_contract import validate_manifest as _validate_manifest

                other_tokens = {
                    n: list(m.manifest.iter_command_tokens() or [])
                    for n, m in self.modules.items()
                    if getattr(m, "manifest", None) is not None
                }
                for issue in _validate_manifest(manifest, other_plugin_tokens=other_tokens):
                    if issue.severity == "error":
                        logger.error(
                            "plugin │ contract │ %s │ %s: %s",
                            manifest.name,
                            issue.code,
                            issue.message,
                            extra={
                                "gemma_event": "plugin_contract_error",
                                "plugin": manifest.name,
                                "code": issue.code,
                            },
                        )
                    else:
                        logger.warning(
                            "plugin │ contract │ %s │ %s: %s",
                            manifest.name,
                            issue.code,
                            issue.message,
                            extra={
                                "gemma_event": "plugin_contract_warning",
                                "plugin": manifest.name,
                                "code": issue.code,
                            },
                        )
            except Exception as _e:
                logger.debug("plugin contract validation failed for %s: %s", manifest.name, _e)

            module_instance = ModuleInstance(
                name=manifest.name,
                manifest=manifest,
                module_class=None,  # ленивый импорт при первом enable_module()
            )

            self.modules[manifest.name] = module_instance

            logger.info(
                "plugin │ register │ %s │ manifest ok",
                manifest.name,
                extra={"gemma_event": "plugin_register", "plugin": manifest.name},
            )
            return module_instance

        except Exception as e:
            logger.error(
                "plugin │ register │ %s │ %s",
                module_path.name,
                e,
                extra={"gemma_event": "plugin_register_fail", "plugin": module_path.name},
            )
            return None

    # ==========================
    #   HOT INSTALL (без рестарта процесса)
    # ==========================
    def hot_install_module(self, module_dir_name: str) -> Dict[str, Any]:
        """
        Загрузить/перезагрузить один плагин с диска (после SelfProgramming.generate_module или правок файлов).
        Сбрасывает кэш importlib для пакета modules.<dir> и включает модуль в loaded_modules.
        """
        with _hot_install_lock:
            return self._hot_install_module_locked(module_dir_name)

    def _hot_install_module_locked(self, module_dir_name: str) -> Dict[str, Any]:
        """hot_install_module body — must run under _hot_install_lock."""
        path = self.modules_path / module_dir_name
        if not path.is_dir():
            return {"success": False, "error": f"not a directory: {path}"}
        manifest_path = path / "module.json"
        if not manifest_path.exists():
            return {"success": False, "error": "module.json missing"}

        manifest_name: Optional[str] = None
        entry_mod: Optional[str] = None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            manifest_name = str(data.get("name") or "").strip() or None
            ep = str(data.get("entrypoint") or "")
            if ":" in ep:
                entry_mod = ep.rsplit(":", 1)[0].strip()
        except Exception as e:
            return {"success": False, "error": f"manifest: {e}"}

        logical_name = manifest_name or module_dir_name

        if logical_name in self.loaded_modules:
            self.disable_module(logical_name)
        if logical_name in self.modules:
            del self.modules[logical_name]

        try:
            for cache_dir in path.rglob("__pycache__"):
                if cache_dir.is_dir():
                    for child in cache_dir.iterdir():
                        try:
                            child.unlink()
                        except OSError:
                            pass
                    try:
                        cache_dir.rmdir()
                    except OSError:
                        pass
        except OSError:
            pass

        if entry_mod and "." in entry_mod:
            purge_prefix = ".".join(entry_mod.split(".")[:-1])
        elif entry_mod:
            purge_prefix = entry_mod
        else:
            purge_prefix = f"modules.{module_dir_name}"
        stale = [k for k in list(sys.modules.keys()) if k == purge_prefix or k.startswith(purge_prefix + ".")]
        root_pkg = purge_prefix.split(".")[0] if purge_prefix else ""
        # Для корня != `modules` сбрасываем весь топ-уровень импорта (тесты; кастомные деревья).
        # Если несколько плагинов делят один корень (не `modules`) — hot-install одного сбросит всех.
        if root_pkg and root_pkg != "modules":
            stale.extend(
                [k for k in list(sys.modules.keys()) if k == root_pkg or k.startswith(root_pkg + ".")]
            )
        seen = set()
        for k in stale:
            if k in seen:
                continue
            seen.add(k)
            try:
                del sys.modules[k]
            except KeyError:
                pass

        # Сбросить кэш ленивого импорта для этого entrypoint
        if entry_mod:
            stale_cache_keys = [k for k in list(_lazy_import_cache.keys()) if entry_mod in k]
            for k in stale_cache_keys:
                _lazy_import_cache.pop(k, None)

        mi = self.load_module(path)
        if not mi:
            return {"success": False, "error": "load_module failed", "module": logical_name}
        if not self.enable_module(mi.name):
            return {"success": False, "error": "enable_module failed", "module": mi.name}
        MONITOR.inc("plugin_hot_install_ok")
        out: Dict[str, Any] = {
            "success": True,
            "module": mi.name,
            "message": f"hot-installed {mi.name}",
        }
        raw_probe = (os.getenv("PLUGIN_HOT_PROBE_AFTER_INSTALL") or "true").strip().lower()
        if raw_probe in {"1", "true", "yes", "on"}:
            out["probe"] = self._hot_install_health_probe(mi.name)
        return out

    def _hot_install_health_probe(self, logical_name: str) -> Dict[str, Any]:
        w = self.get_module(logical_name)
        if not w:
            return {"ok": False, "reason": "module_not_registered"}
        st = getattr(getattr(w, "state", None), "status", None)
        ok = st == "healthy"
        return {"ok": ok, "status": st}

    # ==========================
    #   ENABLE MODULE
    # ==========================
    def enable_module(self, name: str, config: Dict[str, Any] = None) -> bool:
        """Включить модуль"""
        module = self.get_module(name)
        if not module:
            logger.warning(
                "plugin │ enable │ %s │ not found",
                name,
                extra={"gemma_event": "plugin_missing", "plugin": name},
            )
            return False

        try:
            module.load(config)
            self.loaded_modules[name] = module

            bus.emit("module.enabled", {"module": name, "state": module.state})
            logger.info(
                "plugin │ enabled │ %s │ ok",
                name,
                extra={"gemma_event": "plugin_enabled", "plugin": name},
            )
            return True

        except Exception as e:
            logger.error(
                "plugin │ enabled │ %s │ %s",
                name,
                e,
                extra={"gemma_event": "plugin_enable_fail", "plugin": name},
            )
            return False

    # ==========================
    #   DISABLE MODULE
    # ==========================
    def disable_module(self, name: str) -> bool:
        """Отключить модуль"""
        module = self.get_module(name)
        if not module:
            logger.warning(f"Module {name} not found")
            return False

        try:
            module.unload()
            if name in self.loaded_modules:
                del self.loaded_modules[name]

            bus.emit("module.disabled", {"module": name, "state": module.state})
            return True

        except Exception as e:
            logger.error(f"Failed to disable module {name}: {e}")
            return False

    # ==========================
    #   UPDATE CONFIG
    # ==========================
    def update_module_config(self, name: str, config: Dict[str, Any]) -> bool:
        """Обновить конфиг модуля"""
        module = self.get_module(name)
        if not module:
            logger.warning(f"Module {name} not found")
            return False

        try:
            module.config = config
            if name in self.loaded_modules:
                module.unload()
                module.load(config)
            return True
        except Exception as e:
            logger.error(f"Failed to update config for module {name}: {e}")
            return False

    # ==========================
    #   GETTERS
    # ==========================
    def get_module(self, name: str) -> Optional[ModuleInstance]:
        return self.modules.get(name)

    def get_modules(self, filter_type: str = None) -> List[ModuleInstance]:
        if filter_type:
            return [m for m in self.modules.values() if m.manifest.type == filter_type]
        return list(self.modules.values())

    def get_module_states(self) -> List[ModuleState]:
        return [module.state for module in self.modules.values()]

    def get_system_state(self) -> SystemState:
        return SystemState(
            mode="full",
            modules=self.get_module_states(),
            resources={}
        )

    # ==========================
    #   POLICY FILTER
    # ==========================
    def get_allowed_modules(self, context: Dict[str, Any]) -> List[ModuleInstance]:
        """Возвращает ТОЛЬКО включённые модули"""
        return list(self.loaded_modules.values())


# ── Singleton для healers / auto_rollback (main.py вызывает set_plugin_registry) ──

_registry_singleton: Optional["PluginRegistry"] = None


def set_plugin_registry(reg: Optional["PluginRegistry"]) -> None:
    global _registry_singleton
    _registry_singleton = reg


def get_plugin_registry() -> Optional["PluginRegistry"]:
    return _registry_singleton


class _PluginRegistryShim:
    """Обратная совместимость: from core.plugin_registry import plugin_registry."""

    def disable_module(self, name: str) -> bool:
        reg = get_plugin_registry()
        if reg is None:
            logger.debug("plugin_registry shim: registry not bound, disable %s skipped", name)
            return False
        return reg.disable_module(name)

    def enable_module(self, name: str) -> bool:
        reg = get_plugin_registry()
        if reg is None:
            logger.debug("plugin_registry shim: registry not bound, enable %s skipped", name)
            return False
        return reg.enable_module(name)


plugin_registry = _PluginRegistryShim()
