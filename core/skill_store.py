"""
Skill Crystallization — превращает удачные пути (strategy_path_memory) в именованные,
многократно вызываемые скиллы. LLM может создавать, читать и вызывать скиллы
через TOOL_CALL.

Два механизма:
  1. Явный: LLM вызывает skill_save / skill_list / skill_get / skill_delete / skill_run.
  2. Автоматический: при 3+ успешных срабатываниях похожей стратегии —
     strategy_path_memory создаёт скилл сам.

Особенности:
  - Имена скиллов: пробелы автоматически заменяются на _ (TOOL_CALL-safe).
  - Минимальная длина шагов: 20 символов (короткие стратегии тоже работают).
  - Счётчики для авто-кристаллизации кешируются в памяти — не читается файл.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SKILLS_LOCK = threading.Lock()
_AUTO_COUNTER_CACHE: Dict[str, int] = {}
_AUTO_COUNTER_LOCK = threading.Lock()

# How many similar strategy_path hits before auto-crystallization
_AUTO_CRYSTALLIZE_THRESHOLD = 3


def _storage_path() -> str:
    p = (os.getenv("GEMMA_SKILLS_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "skills.json")


def _sanitize_name(raw: str) -> str:
    """Пробелы → _, лишние символы убираются."""
    s = raw.strip().lower().replace(" ", "_")
    return re.sub(r"[^a-zа-яё0-9_\-]", "", s)[:60]


def _load_skills() -> Dict[str, Dict[str, Any]]:
    path = _storage_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return raw
    except Exception as e:
        logger.debug("[skills] load failed: %s", e)
    return {}


def _save_skills(skills: Dict[str, Dict[str, Any]]) -> None:
    path = _storage_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with _SKILLS_LOCK:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(skills, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("[skills] save failed: %s", e)


class SkillStoreModule:
    """Инструменты управления именованными скиллами."""

    BRAIN_LITE_INCLUDE = True

    # ── skill_save ──

    async def skill_save(
        self,
        name: str,
        description: str = "",
        steps: str = "",
        category: str = "general",
    ) -> Dict[str, Any]:
        """
        Сохранить новый скилл (именованный план шагов).
        Если name уже существует — обновляет.
        Пробелы в name заменяются на _.
        steps: пошаговый план, 20-2000 символов.
        """
        name = _sanitize_name(name)
        if not name or len(name) < 3 or len(name) > 60:
            return {"ok": False, "error": "name must be 3-60 chars after sanitization"}
        steps = (steps or "").strip()
        if not steps:
            return {"ok": False, "error": "steps required (20-2000 chars)"}
        if len(steps) < 20 or len(steps) > 2000:
            return {"ok": False, "error": "steps must be 20-2000 characters"}

        skills = _load_skills()
        now = time.time()
        existing = name in skills
        skills[name] = {
            "name": name,
            "description": (description or "").strip()[:300],
            "steps": steps,
            "category": (category or "general").strip()[:40],
            "created_at": skills.get(name, {}).get("created_at", now) if existing else now,
            "updated_at": now,
            "times_used": skills.get(name, {}).get("times_used", 0),
        }
        _save_skills(skills)
        return {
            "ok": True,
            "skill": name,
            "action": "updated" if existing else "created",
            "times_used": skills[name]["times_used"],
        }

    # ── skill_list ──

    async def skill_list(
        self,
        category: str = "",
    ) -> Dict[str, Any]:
        """
        Список всех сохранённых скиллов.
        category — фильтр (по умолчанию все).
        """
        skills = _load_skills()
        items: List[Dict[str, Any]] = []
        for name, s in sorted(skills.items()):
            if category and s.get("category") != category:
                continue
            items.append({
                "name": name,
                "description": str(s.get("description") or "")[:120],
                "category": s.get("category", "general"),
                "times_used": s.get("times_used", 0),
                "updated_at": s.get("updated_at"),
            })
        return {
            "ok": True,
            "count": len(items),
            "skills": items,
        }

    # ── skill_get ──

    async def skill_get(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """
        Получить полный скилл по имени (все шаги).
        name — имя скилла.
        """
        name = name.strip()
        if not name:
            return {"ok": False, "error": "name required"}
        skills = _load_skills()
        # try sanitized too
        s = skills.get(name) or skills.get(_sanitize_name(name))
        if not s:
            return {"ok": False, "error": f"skill '{name}' not found", "known_skills": list(skills.keys())[:20]}
        return {
            "ok": True,
            "skill": {
                "name": s["name"],
                "description": s.get("description", ""),
                "steps": s["steps"],
                "category": s.get("category", "general"),
                "times_used": s.get("times_used", 0),
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
            },
        }

    # ── skill_run ──

    async def skill_run(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """
        Выполнить именованный скилл: вернуть его шаги для исполнения.
        LLM получает шаги и должна следовать им.
        """
        name = name.strip()
        if not name:
            return {"ok": False, "error": "name required"}
        skills = _load_skills()
        s = skills.get(name) or skills.get(_sanitize_name(name))
        if not s:
            return {"ok": False, "error": f"skill '{name}' not found", "known_skills": list(skills.keys())[:20]}

        # Record usage
        actual_name = name if name in skills else _sanitize_name(name)
        skills[actual_name]["times_used"] = (skills[actual_name].get("times_used", 0) or 0) + 1
        _save_skills(skills)

        return {
            "ok": True,
            "skill": actual_name,
            "description": s.get("description", ""),
            "steps": s["steps"],
            "times_used": skills[actual_name]["times_used"],
            "instruction": f"Следуй шагам скилла '{actual_name}' для ответа пользователю.",
        }

    # ── skill_delete ──

    async def skill_delete(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """
        Удалить скилл по имени.
        name — имя скилла для удаления.
        """
        name = name.strip()
        if not name:
            return {"ok": False, "error": "name required"}
        skills = _load_skills()
        actual = name if name in skills else (_sanitize_name(name) if _sanitize_name(name) in skills else None)
        if actual is None:
            return {
                "ok": False,
                "error": f"skill '{name}' not found",
                "known_skills": list(skills.keys())[:20],
            }
        del skills[actual]
        _save_skills(skills)
        return {"ok": True, "skill": name, "action": "deleted"}


# ── Auto-crystallization trigger (вызывается из strategy_path_memory) ──


def _auto_count_incr(fp: str) -> int:
    """In-memory counter: без чтения файла каждый раз."""
    with _AUTO_COUNTER_LOCK:
        _AUTO_COUNTER_CACHE[fp] = _AUTO_COUNTER_CACHE.get(fp, 0) + 1
        return _AUTO_COUNTER_CACHE[fp]


def _auto_count_reset(fp: str) -> None:
    with _AUTO_COUNTER_LOCK:
        _AUTO_COUNTER_CACHE.pop(fp, None)


def auto_crystallize(
    *,
    fp: str,
    intent: str,
    module: str,
    steps_summary: str,
    assistant_excerpt: str,
) -> Optional[str]:
    """
    Автоматическая кристаллизация скилла при повторяющихся успешных путях.

    Использует in-memory кеш счётчиков — не читает strategy_paths.jsonl.
    """
    if not steps_summary or not assistant_excerpt:
        return None
    count = _auto_count_incr(fp)
    if count < _AUTO_CRYSTALLIZE_THRESHOLD:
        return None

    # Reset counter so it doesn't re-trigger on every subsequent hit
    _auto_count_reset(fp)

    skill_name = _sanitize_name(f"auto_{fp[:8]}")
    description = f"Авто-скилл: {intent}/{module} ({count} успешных применений)"
    steps = (
        f"Задача: {intent}, модуль: {module}.\n"
        f"План из успешного опыта:\n{steps_summary[:400]}\n"
        f"Пример удачного ответа: {assistant_excerpt[:200]}"
    )
    if len(steps) < 20:
        steps = steps + "\nСледуй плану шаг за шагом."

    skills = _load_skills()
    if skill_name in skills:
        skills[skill_name]["times_used"] += 1
    else:
        skills[skill_name] = {
            "name": skill_name,
            "description": description,
            "steps": steps,
            "category": "auto",
            "created_at": time.time(),
            "updated_at": time.time(),
            "times_used": 1,
        }
    _save_skills(skills)
    logger.info("[skills] auto-crystallized skill: %s", skill_name)
    return skill_name
