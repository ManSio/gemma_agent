"""
Детектор намерения сгенерировать плагин (SelfProgramming.generate_module) из естественного языка
и подбор уникальных имён/команд без пересечения с уже установленными модулями.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _modules_root() -> Path:
    raw = (os.getenv("MODULES_PATH") or "./modules").strip()
    return Path(raw).resolve()


def collect_reserved_command_tokens() -> Set[str]:
    """Токены slash-команд (без /, lower) из всех module.json под modules/."""
    out: Set[str] = set()
    root = _modules_root()
    if not root.is_dir():
        return out
    for p in root.glob("*/module.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("skip manifest %s: %s", p, e)
            continue
        cmds = data.get("commands") or []
        if not isinstance(cmds, list):
            continue
        for c in cmds:
            if isinstance(c, str):
                t = c.strip().lstrip("/").split("@")[0].lower()
                if t:
                    out.add(t)
            elif isinstance(c, dict):
                for key in ("trigger", "command", "name"):
                    val = c.get(key)
                    if isinstance(val, str) and val.strip():
                        t = val.strip().lstrip("/").split("@")[0].lower()
                        if t:
                            out.add(t)
                        break
    return out


def collect_existing_module_dir_names() -> Set[str]:
    root = _modules_root()
    if not root.is_dir():
        return set()
    return {d.name.lower() for d in root.iterdir() if d.is_dir() and (d / "module.json").is_file()}


def _slug_alnum(s: str, max_len: int = 32) -> str:
    t = re.sub(r"[^a-z0-9_]+", "_", (s or "").lower().strip("_"))
    t = re.sub(r"_+", "_", t).strip("_")
    if not t:
        t = "mod"
    return t[:max_len].rstrip("_") or "mod"


def _unique_module_name(base: str, existing: Set[str]) -> str:
    slug = _slug_alnum(base, 28)
    if slug not in existing:
        return slug
    for i in range(2, 50):
        cand = f"{slug}_{i}"
        if cand not in existing:
            return cand
    h = hashlib.sha256(slug.encode()).hexdigest()[:6]
    return f"{slug}_{h}"


def _unique_cmd_prefix(base: str, reserved: Set[str]) -> str:
    """
    Короткий префикс для команд /{prefix}_new и т.д. (только [a-z0-9_], без ведущего _).
    """
    raw = _slug_alnum(base, 14).replace("_", "")
    if not raw:
        raw = "g"
    if len(raw) < 3:
        raw = (raw + "game")[:12]
    raw = raw[:12]
    candidates = [raw]
    if not raw.endswith("g"):
        candidates.append((raw + "g")[:12])
    for c in candidates:
        probe = f"{c}_new"
        if probe not in reserved:
            return c
    for i in range(0, 256):
        h = hashlib.sha1(f"{raw}:{i}".encode()).hexdigest()[:4]
        c = f"{raw[:8]}{h}"
        if f"{c}_new" not in reserved:
            return c
    return f"g{hashlib.sha256(raw.encode()).hexdigest()[:6]}"


_CROC_RE = re.compile(
    r"(?i)(крокодил|crocodile|\bcroc\b|угадай\s+слов|жестами|игр[ауеы]\s+«?крокодил)",
)


_SELF_PROG_RE = re.compile(
    r"(?i)(selfprogramming\.generate_module|self_programming\.generate_module|"
    r"\bgenerate_module\b|создай\s+(модуль|плагин)|"
    r"новый\s+(модуль|плагин)|напиши\s+(модуль|плагин)|"
    r"задействуй\s+плагин|hot_install|расширь\s+бота)",
)

_GEN_VERB_RE = re.compile(r"(?i)\b(сгенерир(уй|ировать|ируй)|generate)\b")

# «Сгенерируй X» + слабый контекст давал ложные срабатывания на математику («модуль» = abs, «нулевая точка»).
_PLUGIN_GEN_STRONG_RE = re.compile(
    r"(?i)(\bплагин(а|ы|ом|ов)?\b|module\.json|hot_install|hot-install|\bselfprogramming\b|"
    r"entrypoint|manifest\b|/\s*[a-z0-9_]{2,}|slash[- ]?команд|"
    r"модул[ьяей]\s+(для|под|в)\s+(бот|телеграм|telegram|aiogram)|"
    r"(бот|телеграм|telegram|aiogram)\s+.{0,48}модул|(бот|телеграм|telegram|aiogram)\s+.{0,48}плагин|"
    r"новый\s+модуль\s+(для|под|в)\s+(бот|телеграм)|"
    r"сгенерир\w*.{0,24}\bплагин|сгенерир\w*.{0,80}module\.json)",
)

_MATH_NOT_PLUGIN_GEN_RE = re.compile(
    r"(?i)(модул[ьяей]\s+(числ|вектор|разност|комплекс)|модул[ьяей]\s+функц|функц\w*.{0,40}модул|"
    r"график\w*.{0,40}модул|модул[ьяей]\s+в\s+точк|нулев(ой|ая|ую)\s+точк|точк[аи]\s+нул|"
    r"абсолютн\w*\s+значен|\|x\||аргумент\s+комплекс|предел\w*.{0,30}точк)",
)

_PLUGIN_CONTEXT_RE = re.compile(
    r"(?i)\b(модул(ь|я|ем|и)|плагин(а|ы|ом|ов)?|команд(а|ы|у|ой)|"
    r"bot|бота|бот|telegram|aiogram|module\.json|entrypoint|hot_install)\b"
)


_PLUGIN_DEV_GENERAL_RE = re.compile(
    r"(?i)(module\.json|entrypoint|pip_requirements|capabilities\b|input_types\b|"
    r"output_types\b|selfprogramming|generate_module|execute\s*\(|async\s+def\s+execute|"
    r"TOOL_CALL|tool_call|plugins?\s+для|новый\s+модуль|новый\s+плагин|создай\s+модуль|создай\s+плагин|"
    r"сгенерир|напиши\s+модуль|напиши\s+плагин|hot_install|hot-install|manifest\b|"
    r"modules/[a-z0-9_./-]+|import\s+core\.models)",
)


def plugin_programming_prefers_general(user_text: str) -> bool:
    """
    Текст похож на обсуждение/генерацию плагина — не отдавать в math (/calc),
    даже если есть цифры и операторы (примеры кода, версии пакетов).
    """
    t = (user_text or "").strip()
    if not t:
        return False
    if user_signals_generate_module(t):
        return True
    return bool(_PLUGIN_DEV_GENERAL_RE.search(t))


def user_signals_generate_module(user_text: str) -> bool:
    t = (user_text or "").strip()
    if not t:
        return False
    if _SELF_PROG_RE.search(t):
        return True
    # "Сгенерируй X" — только при явном плагинном контексте, не «модуль» из школьной математики.
    if _GEN_VERB_RE.search(t) and _PLUGIN_GEN_STRONG_RE.search(t) and not _MATH_NOT_PLUGIN_GEN_RE.search(t):
        return True
    if _CROC_RE.search(t) and any(
        w in t.lower()
        for w in (
            "модуль",
            "плагин",
            "команда",
            "inline",
            "инлайн",
            "кнопк",
            "групп",
            "бот",
            "aiogram",
            "telegram",
            "созда",
            "сгенер",
            "напиши",
            "сделай",
        )
    ):
        return True
    return False


def is_crocodile_game_intent(user_text: str) -> bool:
    return bool(_CROC_RE.search(user_text or ""))


def build_generate_module_request(user_text: str, *, group_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Возвращает аргументы для SelfProgramming.generate_module или None если сигнал слабый.
    """
    if not user_signals_generate_module(user_text):
        return None
    existing_dirs = collect_existing_module_dir_names()
    reserved = collect_reserved_command_tokens()

    croc = is_crocodile_game_intent(user_text)
    if croc:
        base_name = "group_crocodile_game"
        desc = (
            "Мини-игра «Крокодил» для группы Telegram: ведущий объясняет слово жестами/рисунком "
            "(вне чата), остальные угадывают через команду guess. Секрет слова показывается ведущему "
            "в спойлере в общем сообщении (Telegram ||спойлер||). Стек: платформенный модуль execute(), "
            "aiogram 3.x совместимые команды. Inline-кнопки через manifest buttons → те же команды."
        )
    else:
        base_name = "user_requested_plugin"
        desc = (user_text or "").strip()[:1200]
        if len((user_text or "").strip()) > 1200:
            desc += "…"

    module_name = _unique_module_name(base_name, existing_dirs)
    prefix = _unique_cmd_prefix(module_name, reserved)
    # зарезервируем наши будущие токены, если несколько генераций подряд в одном процессе
    reserved = set(reserved)

    def _tok(action: str) -> str:
        return f"{prefix}_{action}"

    for a in ("new", "guess", "hint", "cancel", "rules"):
        reserved.add(_tok(a))

    if croc:
        commands: List[Any] = [
            {"trigger": _tok("new"), "description": "Новый раунд; вы становитесь ведущим"},
            {"trigger": _tok("guess"), "description": "Угадать слово: /…_guess слово"},
            {"trigger": _tok("hint"), "description": "Подсказка к загадке"},
            {"trigger": _tok("cancel"), "description": "Отменить текущий раунд"},
            {"trigger": _tok("rules"), "description": "Краткие правила"},
        ]
        buttons: List[Dict[str, str]] = [
            {"name": "new_round", "label": "🐊 Новый раунд", "simulate_text": f"/{_tok('new')}"},
            {"name": "hint_btn", "label": "💡 Подсказка", "simulate_text": f"/{_tok('hint')}"},
            {"name": "rules_btn", "label": "📖 Правила", "simulate_text": f"/{_tok('rules')}"},
        ]
    else:
        from core.self_programming import _augment_commands_and_buttons, _infer_domain_template

        domain_template = _infer_domain_template(desc, module_name)
        commands = [{"trigger": _tok("run"), "description": "Запуск модуля"}]
        buttons: List[Dict[str, str]] = []
        commands, buttons = _augment_commands_and_buttons(
            commands,
            buttons,
            domain_template=domain_template,
            module_name=module_name,
        )
        for c in commands:
            tr = str((c or {}).get("trigger") or "").strip().lstrip("/").split("@")[0].lower()
            if tr:
                reserved.add(tr)

    return {
        "module_name": module_name,
        "description": desc,
        "commands": commands,
        "buttons": buttons,
        "command_prefix": prefix,
        "is_crocodile": croc,
        "for_group": bool(group_id),
    }
