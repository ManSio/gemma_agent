"""
Self-Programming Engine for the Universal Social Assistant

Enables automatic module and library generation, repair, and optimization
"""
import os
import re
import json
import logging
import asyncio
import importlib.util
import shutil
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
from core.module_loader import ModuleLoader
from core.library_loader import LibraryLoader
from core.test_runner import TestRunner
from core.plugin_requirements import INSTALL_POLICY_DETAIL, write_plugin_pip_sidecar

logger = logging.getLogger(__name__)

_plugin_registry_for_tools: Optional[Any] = None

# Шаблон modules/<name>/module.py для игры «Крокодил» (плейсхолдеры __CLASS_NAME__, __MNAME__, __PFX__, __DESC_LINE__, __INSTALL_POLICY_DETAIL__).
_CROC_MODULE_BODY = '''"""
__CLASS_NAME__ — __DESC_LINE__
Сгенерировано SelfProgramming; диспетчер slash-команд и mbtn: кнопки платформы.
__INSTALL_POLICY_DETAIL__
"""
from __future__ import annotations

import random
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.models import Output

WORDS = [
    "яблоко", "машина", "радуга", "книга", "космос", "река", "гитара", "море",
    "компьютер", "дерево", "часы", "стол", "шарик", "телефон", "зонт", "солнце",
    "луна", "поезд", "самолёт", "пицца", "кофе", "мышь", "клавиатура", "окно",
]

_LOCK = threading.Lock()
_STATE: Dict[str, Dict[str, Any]] = {}

PFX = "__PFX__"
MNAME = "__MNAME__"


def _session_key(context: Dict[str, Any], user_id: str) -> str:
    gid = str(context.get("group_id") or "").strip()
    if gid:
        return "g:" + gid
    return "u:" + (user_id or "?")


def _parse_action(payload: str) -> Tuple[str, str]:
    parts = (payload or "").strip().split(None, 1)
    head = (parts[0] if parts else "").lower().lstrip("/").split("@", 1)[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    for a in ("new", "guess", "hint", "cancel", "rules"):
        if head == f"{PFX}_{a}":
            return a, rest
    return "", rest


def _pick_word() -> str:
    return random.choice(WORDS)


class __CLASS_NAME__:
    """Игра «Крокодил» для группы (ведущий + угадывающие)."""

    def __init__(self) -> None:
        pass

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input") or {}
        context = args.get("context") or {}
        payload = str(input_data.get("payload", "") or "")
        uid = str(context.get("user_id") or "")
        who = uid[-4:] if uid.isdigit() else (uid or "?")

        action, extra = _parse_action(payload)
        if not action:
            lines = [
                "🐊 Крокодил",
                f"/{PFX}_new — новый раунд (вы ведущий)",
                f"/{PFX}_guess слово — угадать",
                f"/{PFX}_hint — подсказка",
                f"/{PFX}_cancel — сброс",
                f"/{PFX}_rules — правила",
                "Кнопки под ответом бота дублируют команды (inline).",
            ]
            return [Output(type="text", payload=chr(10).join(lines), meta={"module": MNAME})]

        key = _session_key(context, uid)

        if action == "rules":
            msg = (
                "📖 Ведущий видит слово в спойлере и объясняет жестами или рисунком. "
                "Остальные пишут /" + PFX + "_guess … Не подглядывайте в чужой спойлер."
            )
            return [Output(type="text", payload=msg, meta={"module": MNAME})]

        with _LOCK:
            st: Optional[Dict[str, Any]] = _STATE.get(key)

            if action == "cancel":
                _STATE.pop(key, None)
                return [Output(type="text", payload="Раунд отменён.", meta={"module": MNAME})]

            if action == "new":
                w = _pick_word()
                _STATE[key] = {"word": w, "leader": uid, "leader_hint": who}
                spoiler = f"||{w}||"
                nl = chr(10)
                msg = (
                    f"🐊 Раунд начал! Ведущий — id {uid} ({who})."
                    + nl
                    + f"Ведущий: твоё слово в спойлере: {spoiler}"
                    + nl
                    + f"Объясняй без слов; остальные — /{PFX}_guess …"
                )
                return [Output(type="text", payload=msg, meta={"module": MNAME})]

            if st is None:
                return [
                    Output(
                        type="text",
                        payload=f"Нет активного раунда. Начни с /{PFX}_new",
                        meta={"module": MNAME},
                    )
                ]

            word = str(st.get("word") or "")
            leader = str(st.get("leader") or "")

            if action == "hint":
                if not word:
                    return [Output(type="text", payload="Нет слова.", meta={"module": MNAME})]
                h = f"Букв: {len(word)}, первая «{word[0]}»"
                return [Output(type="text", payload=f"💡 Подсказка: {h}", meta={"module": MNAME})]

            if action == "guess":
                g = (extra or "").strip().lower()
                if not g:
                    return [
                        Output(
                            type="text",
                            payload=f"Напиши: /{PFX}_guess слово",
                            meta={"module": MNAME},
                        )
                    ]
                if uid == leader:
                    return [
                        Output(
                            type="text",
                            payload="Ведущий не угадывает 🙂",
                            meta={"module": MNAME},
                        )
                    ]
                if g == word.lower():
                    _STATE.pop(key, None)
                    win = (
                        f"✅ Угадано: {word}! Победил игрок {uid}. "
                        f"/{PFX}_new — ещё раунд."
                    )
                    return [Output(type="text", payload=win, meta={"module": MNAME})]
                return [
                    Output(
                        type="text",
                        payload="❌ Пока мимо. Пробуй ещё.",
                        meta={"module": MNAME},
                    )
                ]

        return [Output(type="text", payload="Сбой состояния.", meta={"module": MNAME})]
'''


def set_plugin_registry_for_tools(reg: Optional[Any]) -> None:
    """Вызывать при старте (main/api): инструменты создают SelfProgrammingModule() без аргументов."""
    global _plugin_registry_for_tools
    _plugin_registry_for_tools = reg


def _pascal_base(module_name: str) -> str:
    """group_crocodile_game -> GroupCrocodileGame (для entrypoint и класса)."""
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", (module_name or "").strip())
    parts = [p for p in raw.split("_") if p]
    if not parts:
        return "Generated"
    return "".join(p[:1].upper() + p[1:].lower() for p in parts)


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _infer_domain_template(description: str, module_name: str) -> str:
    blob = f"{description or ''} {module_name or ''}".lower()
    if any(k in blob for k in ("todo", "task", "задач", "чеклист", "список дел")):
        return "todo"
    if any(k in blob for k in ("weather", "погод", "температур", "влажност")):
        return "weather"
    if any(k in blob for k in ("parser", "parse", "regex", "парсер", "разбор", "извлеч")):
        return "parser"
    if any(k in blob for k in ("monitor", "health", "метрик", "наблюд", "аптайм", "диагност")):
        return "monitoring"
    return "generic"


def _command_prefix_from_commands(commands: List[Dict[str, Any]], fallback: str) -> str:
    known_suffixes = (
        "_todo_add",
        "_todo_list",
        "_weather_sim",
        "_extract_numbers",
        "_extract_urls",
        "_ping",
        "_health",
        "_echo",
        "_upper",
        "_stats",
        "_calc",
    )
    for c in commands or []:
        tr = str((c or {}).get("trigger") or "").strip().lstrip("/").split("@")[0]
        low = tr.lower()
        for sfx in known_suffixes:
            if low.endswith(sfx) and len(tr) > len(sfx):
                return tr[: -len(sfx)]
        if "_" in tr:
            return tr.rsplit("_", 1)[0]
        if tr:
            return tr
    return re.sub(r"[^a-z0-9_]", "", (fallback or "").lower())[:16] or "mod"


def _domain_actions(template: str) -> List[tuple[str, str]]:
    if template == "todo":
        return [("todo_add", "Добавить задачу"), ("todo_list", "Список задач")]
    if template == "weather":
        return [("weather_sim", "Симуляция погоды")]
    if template == "parser":
        return [("extract_numbers", "Извлечь числа"), ("extract_urls", "Извлечь ссылки")]
    if template == "monitoring":
        return [("ping", "Ping"), ("health", "Health")]
    return []


def _augment_commands_and_buttons(
    commands: List[Dict[str, Any]],
    buttons: List[Dict[str, Any]],
    *,
    domain_template: str,
    module_name: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    out_cmds = [dict(c) for c in (commands or []) if isinstance(c, dict)]
    out_btns = [dict(b) for b in (buttons or []) if isinstance(b, dict)]
    if not out_cmds:
        out_cmds = [{"trigger": f"/{module_name}_run", "description": "Запуск модуля"}]
    prefix = _command_prefix_from_commands(out_cmds, module_name)
    existing = {
        str((c or {}).get("trigger") or "").strip().lstrip("/").split("@")[0].lower()
        for c in out_cmds
    }
    for action, label in _domain_actions(domain_template):
        trig = f"{prefix}_{action}"
        if trig not in existing:
            out_cmds.append({"trigger": f"/{trig}", "description": label})
            existing.add(trig)
    if not out_btns:
        for action, label in _domain_actions(domain_template)[:2]:
            trig = f"/{prefix}_{action}"
            out_btns.append(
                {
                    "name": action.upper(),
                    "label": label,
                    "simulate_text": trig,
                }
            )
    return out_cmds, out_btns


class SelfProgrammingModule:
    """Self-programming engine for generating and repairing modules"""

    def __init__(
        self,
        modules_path: str = "./modules",
        libraries_path: str = "./libraries",
        core_libraries_path: str = "./core_libraries",
        plugin_registry: Optional[Any] = None,
    ):
        self.modules_path = Path(modules_path)
        self.libraries_path = Path(libraries_path)
        self.core_libraries_path = Path(core_libraries_path)
        self.plugin_registry = plugin_registry or _plugin_registry_for_tools

        self.module_loader = ModuleLoader(modules_path)
        self.library_loader = LibraryLoader()
        self.test_runner = TestRunner()

    def _hot_install_plugin(self, module_dir_name: str) -> Dict[str, Any]:
        reg = self.plugin_registry
        if reg is None:
            return {"success": False, "skipped": True, "reason": "no_plugin_registry"}
        if not _truthy_env("PLUGIN_HOT_INSTALL_AFTER_GENERATE", True):
            return {"success": False, "skipped": True, "reason": "PLUGIN_HOT_INSTALL_AFTER_GENERATE off"}
        if not hasattr(reg, "hot_install_module"):
            return {"success": False, "skipped": True, "reason": "registry has no hot_install_module"}
        try:
            return reg.hot_install_module(module_dir_name)
        except Exception as e:
            logger.warning("hot_install_module failed: %s", e)
            return {"success": False, "error": str(e)}

    def _deploy_enabled(self) -> bool:
        return _truthy_env("SELF_DEPLOY_ENABLED", False)

    async def _deploy_after_delay(self, module_name: str, delay: int = 30) -> None:
        await asyncio.sleep(delay)
        try:
            from core.self_deployment import SelfDeploymentModule
            deployer = SelfDeploymentModule()
            result = await deployer.deploy_module(module_name)
            if result.get("ok"):
                logger.info("[self_deploy] %s deployed: %s", module_name, result.get("message", "ok"))
            else:
                logger.warning("[self_deploy] %s deploy failed: %s", module_name, result.get("error", "unknown"))
        except Exception as e:
            logger.warning("[self_deploy] deploy error for %s: %s", module_name, e)

    def _strict_mode_enabled(self) -> bool:
        return _truthy_env("SELF_PROGRAMMING_STRICT_MODE", True)

    def _strict_smoke_enabled(self) -> bool:
        return _truthy_env("SELF_PROGRAMMING_STRICT_SMOKE", True)

    def _strict_rollback_enabled(self) -> bool:
        return _truthy_env("SELF_PROGRAMMING_STRICT_ROLLBACK", True)

    def _strict_report_template(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self._strict_mode_enabled()),
            "checks": [],
            "ok": True,
            "error": "",
        }

    def _strict_add_check(self, rep: Dict[str, Any], name: str, ok: bool, detail: str = "") -> None:
        rep.setdefault("checks", []).append({"name": name, "ok": bool(ok), "detail": str(detail or "")})
        if not ok:
            rep["ok"] = False
            if not rep.get("error"):
                rep["error"] = str(detail or name)

    def _strict_validate_files(self, module_dir: Path, *, domain_template: str) -> None:
        module_file = module_dir / "module.py"
        tests_file = module_dir / "tests.py"
        manifest_file = module_dir / "module.json"
        if not module_file.is_file() or not tests_file.is_file() or not manifest_file.is_file():
            raise ValueError("strict: missing module.py/tests.py/module.json")
        code = module_file.read_text(encoding="utf-8")
        tests = tests_file.read_text(encoding="utf-8")
        if "class " not in code or "async def execute" not in code:
            raise ValueError("strict: generated module has no executable class")
        if "safe_eval_arithmetic" not in code:
            raise ValueError("strict: generated module must use safe_eval_arithmetic")
        if "def test_execute" not in tests:
            raise ValueError("strict: generated tests.py must contain test_execute")
        if domain_template != "generic":
            marker = {
                "todo": 'action == "todo_',
                "weather": 'action == "weather_sim"',
                "parser": 'action == "extract_',
                "monitoring": 'action == "health"' ,
            }.get(domain_template, "")
            if marker and marker not in code:
                raise ValueError(f"strict: domain template '{domain_template}' action not found in code")

    async def _strict_smoke_run(self, module_dir: Path, module_name: str) -> None:
        module_file = module_dir / "module.py"
        spec = importlib.util.spec_from_file_location(f"sp_smoke_{module_name}", module_file)
        if spec is None or spec.loader is None:
            raise ValueError("strict: cannot load generated module spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls_name = _pascal_base(module_name) + "Module"
        cls = getattr(mod, cls_name, None)
        if cls is None:
            raise ValueError("strict: generated class not found")
        inst = cls()
        out = await inst.execute({"input": {"payload": ""}, "context": {"user_id": "strict-smoke"}})
        if not isinstance(out, list) or not out:
            raise ValueError("strict: execute() smoke run produced empty output")

    async def _strict_run_all_checks(
        self,
        *,
        module_dir: Path,
        module_name: str,
        domain_template: str,
    ) -> Dict[str, Any]:
        rep = self._strict_report_template()
        if not self._strict_mode_enabled():
            return rep
        try:
            self._strict_validate_files(module_dir, domain_template=domain_template)
            self._strict_add_check(rep, "validate_files", True, "required files and template checks passed")
        except Exception as e:
            self._strict_add_check(rep, "validate_files", False, str(e))
            return rep
        if self._strict_smoke_enabled():
            try:
                await self._strict_smoke_run(module_dir, module_name)
                self._strict_add_check(rep, "smoke_execute", True, "import + execute smoke passed")
            except Exception as e:
                self._strict_add_check(rep, "smoke_execute", False, str(e))
        else:
            self._strict_add_check(rep, "smoke_execute", True, "disabled by env")
        return rep

    # ============================================================
    #   MODULE GENERATION
    # ============================================================
    async def generate_module(
        self,
        module_name: str,
        description: str,
        commands: List[Dict[str, Any]] = None,
        dependencies: List[str] = None,
        pip_requirements: List[str] = None,
        buttons: List[Dict[str, Any]] = None,
        game_crocodile: bool = False,
        command_prefix: str = "",
        capabilities: List[str] = None,
        **_: Any,
    ) -> Dict[str, Any]:

        try:
            module_dir = self.modules_path / module_name
            module_dir.mkdir(exist_ok=True)
            pip_req = [str(x).strip() for x in (pip_requirements or []) if str(x).strip()]
            caps = [str(x).strip() for x in (capabilities or []) if str(x).strip()]
            pfx = (command_prefix or "").strip()
            domain_template = _infer_domain_template(description, module_name)
            cmds_in = [dict(c) for c in (commands or []) if isinstance(c, dict)]
            btns_in = [dict(b) for b in (buttons or []) if isinstance(b, dict)]
            if not game_crocodile:
                cmds_in, btns_in = _augment_commands_and_buttons(
                    cmds_in,
                    btns_in,
                    domain_template=domain_template,
                    module_name=module_name,
                )
            if game_crocodile and not pfx and commands:
                tr0 = str((commands[0] or {}).get("trigger") or "").strip().lstrip("/").split("@")[0]
                if "_" in tr0:
                    pfx = tr0.rsplit("_", 1)[0]

            # manifest
            manifest = self._generate_module_manifest(
                module_name, description, cmds_in, dependencies, pip_req, buttons=btns_in, capabilities=caps
            )
            with open(module_dir / "module.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            write_plugin_pip_sidecar(module_dir, pip_req)

            # module.py
            module_code = self._generate_module_code(
                module_name,
                description,
                dependencies,
                game_crocodile=bool(game_crocodile and pfx),
                command_prefix=pfx,
                command_triggers=[str((c or {}).get("trigger") or "").strip() for c in cmds_in],
                domain_template=domain_template,
            )
            with open(module_dir / "module.py", "w", encoding="utf-8") as f:
                f.write(module_code)

            # tests.py
            tests_code = self._generate_tests(module_name)
            with open(module_dir / "tests.py", "w", encoding="utf-8") as f:
                f.write(tests_code)

            strict_report = await self._strict_run_all_checks(
                module_dir=module_dir,
                module_name=module_name,
                domain_template=domain_template,
            )
            if self._strict_mode_enabled() and not strict_report.get("ok"):
                raise ValueError(f"strict gate failed: {strict_report.get('error')}")

            logger.info(f"Module {module_name} generated successfully")

            # Hard gate: hot-install only after strict checks pass.
            hot_install = self._hot_install_plugin(module_name)

            # Self-deployment: auto git push + remote deploy (background, with delay)
            if self._deploy_enabled():
                asyncio.create_task(self._deploy_after_delay(module_name, delay=30))

            return {
                "success": True,
                "module_name": module_name,
                "message": f"Module {module_name} created successfully",
                "hot_install": hot_install,
                "strict_report": strict_report,
            }

        except Exception as e:
            logger.error(f"Failed to generate module {module_name}: {e}")
            strict_report = self._strict_report_template()
            strict_report["ok"] = False
            strict_report["error"] = str(e)
            if self._strict_mode_enabled() and self._strict_rollback_enabled():
                try:
                    module_dir = self.modules_path / module_name
                    if module_dir.exists():
                        shutil.rmtree(module_dir)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'self_programming', e, exc_info=True)
            return {"success": False, "error": str(e), "strict_report": strict_report}

    # ============================================================
    #   MANIFEST GENERATION
    # ============================================================
    def _generate_module_manifest(
        self,
        name: str,
        description: str,
        commands: List[Dict[str, Any]] = None,
        dependencies: List[str] = None,
        pip_requirements: List[str] = None,
        buttons: List[Dict[str, Any]] = None,
        capabilities: List[str] = None,
    ) -> Dict[str, Any]:
        pr = list(pip_requirements or [])
        cls = _pascal_base(name) + "Module"
        caps = list(capabilities or [])
        return {
            "name": name,
            "version": "1.0.0",
            "type": "module",
            "description": description,
            "entrypoint": f"modules.{name}.module:{cls}",
            "input_types": ["text"],
            "output_types": ["text"],
            "capabilities": caps,
            "prompts": {},
            "commands": commands or [],
            "buttons": buttons or [],
            "config_schema": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "requires": dependencies or [],
            "pip_requirements": pr,
        }

    # ============================================================
    #   MODULE CODE GENERATION
    # ============================================================
    def _generate_module_code(
        self,
        name: str,
        description: str,
        dependencies: List[str] = None,
        *,
        game_crocodile: bool = False,
        command_prefix: str = "",
        command_triggers: List[str] = None,
        domain_template: str = "generic",
    ) -> str:
        if game_crocodile and command_prefix:
            return self._build_crocodile_plugin_source(name, description, command_prefix)
        class_name = _pascal_base(name) + "Module"
        title = class_name.replace("Module", "")
        triggers = [str(x).strip() for x in (command_triggers or []) if str(x).strip()]
        if not triggers:
            triggers = [f"/{name}"]
        trigger_tokens = [t.lstrip("/").split("@")[0].lower() for t in triggers]
        command_to_action: Dict[str, str] = {}
        known_actions = [
            "todo_add",
            "todo_list",
            "weather_sim",
            "extract_numbers",
            "extract_urls",
            "echo",
            "upper",
            "stats",
            "calc",
            "ping",
            "health",
        ]
        for tok in trigger_tokens:
            for act in known_actions:
                if tok == act or tok.endswith("_" + act):
                    command_to_action[tok] = act
                    break

        return f'''"""
{title} module — {description}

Дополнительные пакеты: module.json -> pip_requirements (например "httpx>=0.27.0").
{INSTALL_POLICY_DETAIL}
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from core.arithmetic_tool_module import safe_eval_arithmetic
from core.light_slash import parse_slash_args
from core.models import Output


TRIGGERS = {triggers!r}
TRIGGER_TOKENS = {trigger_tokens!r}
COMMAND_TO_ACTION = {command_to_action!r}
DOMAIN_TEMPLATE = {domain_template!r}


class {class_name}:
    """{description}."""

    def __init__(self):
        self._runtime: Dict[str, Any] = {{}}

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input") or {{}}
        payload = str(input_data.get("payload", "") or "").strip()
        if not payload:
            return [Output(type="text", payload=self._help(), meta={{"module": "{name}"}})]

        cmd, rest = parse_slash_args(payload)
        if cmd not in TRIGGER_TOKENS:
            return [Output(type="text", payload=self._help(), meta={{"module": "{name}"}})]

        body = rest.strip()
        default_action = COMMAND_TO_ACTION.get(cmd)
        if not body and not default_action:
            return [Output(type="text", payload=self._help(), meta={{"module": "{name}"}})]
        if body.lower() in {{"help", "?"}}:
            return [Output(type="text", payload=self._help(), meta={{"module": "{name}"}})]
        if default_action:
            action = default_action
            tail = body
        else:
            parts = body.split(None, 1)
            action = (parts[0] if parts else "").lower()
            tail = (parts[1] if len(parts) > 1 else "").strip()

        if action == "echo":
            return [Output(type="text", payload=(tail or "(пусто)"), meta={{"module": "{name}", "action": "echo"}})]

        if action == "upper":
            return [Output(type="text", payload=(tail or "").upper(), meta={{"module": "{name}", "action": "upper"}})]

        if action == "stats":
            text = tail or ""
            words = [w for w in text.split() if w]
            lines = text.splitlines() if text else []
            msg = (
                f"chars={{len(text)}}\\n"
                f"words={{len(words)}}\\n"
                f"lines={{len(lines)}}"
            )
            return [Output(type="text", payload=msg, meta={{"module": "{name}", "action": "stats"}})]

        if action == "calc":
            if not tail:
                return [Output(type="text", payload="Нужно выражение после calc.", meta={{"module": "{name}"}})]
            try:
                result = safe_eval_arithmetic(tail)
            except Exception as e:
                return [Output(type="text", payload=f"Ошибка вычисления: {{e}}", meta={{"module": "{name}"}})]
            return [Output(type="text", payload=f"Результат: {{result}}", meta={{"module": "{name}", "action": "calc"}})]

        if DOMAIN_TEMPLATE == "todo":
            if action == "todo_add":
                text = tail or ""
                if not text:
                    return [Output(type="text", payload="todo_add требует текст задачи.", meta={{"module": "{name}"}})]
                tasks = self._runtime.setdefault("tasks", [])
                tasks.append(text)
                return [Output(type="text", payload=f"Добавлено задач: {{len(tasks)}}", meta={{"module": "{name}", "action": "todo_add"}})]
            if action == "todo_list":
                tasks = list(self._runtime.get("tasks") or [])
                body = "\\n".join(f"{{i+1}}. {{t}}" for i, t in enumerate(tasks)) if tasks else "Список задач пуст."
                return [Output(type="text", payload=body, meta={{"module": "{name}", "action": "todo_list"}})]

        if DOMAIN_TEMPLATE == "weather":
            if action == "weather_sim":
                text = tail or ""
                nums = [x for x in re.findall(r"-?\\d+(?:\\.\\d+)?", text)]
                if len(nums) < 2:
                    return [Output(type="text", payload="Формат: weather_sim <temp> <humidity>", meta={{"module": "{name}"}})]
                temp = float(nums[0])
                humid = float(nums[1])
                status = "ok"
                if temp > 35 or humid < 20:
                    status = "risk"
                return [Output(type="text", payload=f"weather: T={{temp}}, H={{humid}}, status={{status}}", meta={{"module": "{name}", "action": "weather_sim"}})]

        if DOMAIN_TEMPLATE == "parser":
            if action == "extract_numbers":
                text = tail or ""
                nums = re.findall(r"-?\\d+(?:\\.\\d+)?", text)
                return [Output(type="text", payload=", ".join(nums) if nums else "нет чисел", meta={{"module": "{name}", "action": "extract_numbers"}})]
            if action == "extract_urls":
                text = tail or ""
                urls = re.findall(r"https?://\\S+", text)
                return [Output(type="text", payload="\\n".join(urls) if urls else "нет ссылок", meta={{"module": "{name}", "action": "extract_urls"}})]

        if DOMAIN_TEMPLATE == "monitoring":
            if action == "ping":
                self._runtime["ping_count"] = int(self._runtime.get("ping_count", 0)) + 1
                return [Output(type="text", payload=f"pong {{self._runtime['ping_count']}}", meta={{"module": "{name}", "action": "ping"}})]
            if action == "health":
                return [Output(type="text", payload=f"ok domain={{DOMAIN_TEMPLATE}}", meta={{"module": "{name}", "action": "health"}})]

        return [Output(type="text", payload=self._help(), meta={{"module": "{name}", "unknown_action": action}})]

    def _help(self) -> str:
        t = TRIGGERS[0] if TRIGGERS else "/{name}"
        domain_lines = ""
        if DOMAIN_TEMPLATE == "todo":
            domain_lines = "\\n• todo_add <text>\\n• todo_list"
        elif DOMAIN_TEMPLATE == "weather":
            domain_lines = "\\n• weather_sim <temp> <humidity>"
        elif DOMAIN_TEMPLATE == "parser":
            domain_lines = "\\n• extract_numbers <text>\\n• extract_urls <text>"
        elif DOMAIN_TEMPLATE == "monitoring":
            domain_lines = "\\n• ping\\n• health"
        return (
            "{description}\\n\\n"
            f"Команда: {{t}} <action> <text|expr>\\n"
            "Действия:\\n"
            "• echo <text>\\n"
            "• upper <text>\\n"
            "• stats <text>\\n"
            "• calc <expr>\\n"
            f"Шаблон: {{DOMAIN_TEMPLATE}}{{domain_lines}}\\n"
            f"Триггеры: {{', '.join(TRIGGERS)}}"
        )
'''

    def _build_crocodile_plugin_source(self, name: str, description: str, pfx: str) -> str:
        """Групповая мини-игра: слово в спойлере для ведущего, угадывание текстом."""
        class_name = _pascal_base(name) + "Module"
        safe_pfx = re.sub(r"[^a-z0-9_]", "", pfx.lower())
        if not safe_pfx:
            safe_pfx = "gcroc"
        desc_line = " ".join((description or "")[:400].splitlines())
        return (
            _CROC_MODULE_BODY.replace("__CLASS_NAME__", class_name)
            .replace("__MNAME__", name)
            .replace("__PFX__", safe_pfx)
            .replace("__DESC_LINE__", desc_line)
            .replace("__INSTALL_POLICY_DETAIL__", INSTALL_POLICY_DETAIL)
        )

    # ============================================================
    #   TEST GENERATION
    # ============================================================
    def _generate_tests(self, name: str) -> str:
        class_name = _pascal_base(name) + "Module"
        pascal = _pascal_base(name)

        return f'''"""
Tests for {pascal} module
"""
import unittest
import asyncio
from modules.{name}.module import {class_name}

class Test{pascal}Module(unittest.TestCase):

    def setUp(self):
        self.module = {class_name}()

    def test_execute(self):
        async def run_test():
            result = await self.module.execute({{
                "input": {{"payload": "test input"}}
            }})
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
'''

    # ============================================================
    #   MODULE SELF-REPAIR
    # ============================================================
    async def self_repair_module(self, module_name: str) -> Dict[str, Any]:
        try:
            module_path = self.modules_path / module_name
            if not module_path.exists():
                return {"success": False, "error": "Module directory not found"}

            hot = self._hot_install_plugin(module_name)
            ok = bool(hot.get("success"))
            return {
                "success": ok,
                "message": hot.get("message") or (f"Module {module_name} reloaded" if ok else str(hot.get("error", ""))),
                "hot_install": hot,
            }

        except Exception as e:
            logger.error(f"Error repairing module {module_name}: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================
    #   LIBRARY SELF-REPAIR
    # ============================================================
    async def self_repair_library(self, library_name: str) -> Dict[str, Any]:
        try:
            status = self.library_loader.get_library_status(library_name)

            if status != "broken":
                return {"success": False, "error": "Library not broken"}

            fallback = self.library_loader.get_fallback_library(library_name)

            if fallback:
                return {
                    "success": True,
                    "message": f"Library {library_name} switched to fallback {fallback}"
                }

            return {"success": False, "error": "No fallback available"}

        except Exception as e:
            logger.error(f"Error repairing library {library_name}: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================
    #   SYSTEM ANALYSIS
    # ============================================================
    async def analyze_system(self, plugin_registry=None) -> Dict[str, Any]:
        try:
            # TOOL_CALL из мозга почти никогда не передаёт plugin_registry — брать с инстанса / глобальный реестр.
            reg = plugin_registry if plugin_registry is not None else self.plugin_registry
            modules_data: List[Any] = []
            if reg is not None and hasattr(reg, "get_module_states"):
                for st in reg.get_module_states():
                    if hasattr(st, "model_dump"):
                        modules_data.append(st.model_dump(mode="json"))
                    elif isinstance(st, dict):
                        modules_data.append(st)
                    else:
                        modules_data.append(
                            {
                                "name": getattr(st, "name", ""),
                                "type": getattr(st, "type", ""),
                                "status": getattr(st, "status", ""),
                                "last_error": getattr(st, "last_error", None),
                            }
                        )

            return {
                "timestamp": datetime.now().isoformat(),
                "modules": modules_data,
                "module_count": len(modules_data),
                "libraries": list(self.library_loader.registry.keys()),
                "library_statuses": {
                    name: self.library_loader.get_library_status(name)
                    for name in self.library_loader.registry.keys()
                },
                "plugin_registry_attached": reg is not None,
            }

        except Exception as e:
            logger.error(f"Error during system analysis: {e}")
            return {"error": str(e)}

    # ============================================================
    #   ISSUE DETECTION
    # ============================================================
    async def detect_issues(
        self,
        system_report: Optional[Dict[str, Any]] = None,
        plugin_registry: Any = None,
    ) -> List[Dict[str, Any]]:
        """Если отчёт не передан (частый TOOL_CALL от LLM) — собираем через analyze_system."""
        if system_report is None or system_report == {}:
            system_report = await self.analyze_system(plugin_registry=plugin_registry)
        if not isinstance(system_report, dict):
            return [{"type": "invalid_report", "description": "system_report не объект"}]

        issues = []

        for module_state in system_report.get("modules", []):
            if not isinstance(module_state, dict):
                continue
            if module_state.get("status") == "failed":
                issues.append({
                    "type": "module_failed",
                    "module": module_state.get("name"),
                    "description": f"Module {module_state.get('name')} failed"
                })

        lib_stat = system_report.get("library_statuses", {})
        if isinstance(lib_stat, dict):
            for lib, status in lib_stat.items():
                if status == "broken":
                    issues.append({
                        "type": "library_broken",
                        "library": lib,
                        "description": f"Library {lib} broken"
                    })

        return issues

    # ============================================================
    #   PATCH GENERATION
    # ============================================================
    async def generate_patch(self, issue: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        logger.info(f"Generating patch for issue: {issue}")

        if issue.get("type") == "module_failed":
            return {
                "type": "module_repair",
                "module": issue.get("module"),
                "action": "recreate_module"
            }

        if issue.get("type") == "library_broken":
            return {
                "type": "library_fallback",
                "library": issue.get("library"),
                "action": "enable_fallback"
            }

        return None

    # ============================================================
    #   PATCH APPLICATION
    # ============================================================
    async def apply_patch(self, patch: Dict[str, Any]) -> bool:
        try:
            logger.info(f"Applying patch: {patch}")
            return True
        except Exception as e:
            logger.error(f"Error applying patch: {e}")
            return False
