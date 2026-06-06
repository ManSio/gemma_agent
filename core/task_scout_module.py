"""
Разведка задачи «здесь и сейчас»: структурированный план, опора на Mem0 и локальные заметки.

Не управляет браузером, не решает капчи и не эмулирует мышь — только стратегия и обучающие конспекты
(в т.ч. обзор типов защит сайтов на уровне понятий, без инструкций по обходу).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

from core.openrouter_provider import get_openrouter_provider

logger = logging.getLogger(__name__)

_SCOUT_LOCK = threading.Lock()


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def scout_enabled() -> bool:
    return _truthy("TASK_SCOUT_ENABLED", True)


def playbook_path() -> str:
    p = (os.getenv("TASK_SCOUT_PLAYBOOK_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "task_scout_playbooks.jsonl")


def _hosts_from_urls_blob(urls: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(r"https?://[^\s\]>\"')]+", urls or "", re.I):
        try:
            h = urlparse(m.group(0).rstrip(".,;")).hostname
            if h:
                out.append(h.lower())
        except Exception as e:
            logger.debug('%s optional failed: %s', 'task_scout_module', e, exc_info=True)
    return out


def _memories_blob(mem_items: List[Any]) -> str:
    lines: List[str] = []
    for item in mem_items or []:
        if isinstance(item, dict):
            c = item.get("content") or item.get("text") or item.get("memory")
            if c:
                lines.append(str(c).strip()[:500])
        elif isinstance(item, str):
            lines.append(item.strip()[:500])
    if not lines:
        return ""
    return "\n".join(lines[:12])


def _iter_playbooks_reverse(path: str) -> Iterator[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            yield o


def _recall_playbooks_text(domain: str, query: str, limit: int) -> str:
    dom = (domain or "").strip().lower()
    q = (query or "").strip().lower()
    path = playbook_path()
    picked: List[str] = []
    for rec in _iter_playbooks_reverse(path):
        if len(picked) >= max(1, min(limit, 24)):
            break
        d = str(rec.get("domain") or "").lower()
        title = str(rec.get("title") or "")
        body = str(rec.get("body") or "")
        if dom and dom not in d:
            continue
        if q and q not in title.lower() and q not in body.lower():
            continue
        picked.append(f"[{d}] {title}\n{body[:800]}")
    return "\n---\n".join(picked) if picked else ""


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            try:
                o = json.loads(m.group(0))
                return o if isinstance(o, dict) else None
            except json.JSONDecodeError:
                return None
    return None


_SCOUT_SYSTEM = """Ты помощник по планированию доступа к публичной информации и типовым задачам в чат-боте gemma_bot.
Ответь ТОЛЬКО одним JSON-объектом (без markdown), ключи:
- "goal_restated": string — переформулировка цели;
- "steps": array объектов {"step": number, "action_type": string, "detail": string} — 3–8 шагов, конкретно;
- "risks_and_limits": string — честно: что бот НЕ умеет (реальный браузер, капча, движение мыши, логин без данных пользователя);
- "defense_overview": string — краткий нейтральный обзор типов защит (WAF, rate limit, geo, bot detection, CAPTCHA) как понятий, без пошагового обхода;
- "tools_suggested": array строк — из набора: UrlFetch.fetch_page, UniversalSearch.search, LawSearch.search, LawSearch.fetch_act, SiteRecipe.parse_with_recipe, Wikipedia.scan, BooksRAG (если уместно), /filefrom для прямой ссылки на файл, TaskScout.save_playbook_note для сохранить удачную заметку;
- "mem0_context_used": boolean — учёл ли ты переданный блок памяти.

Запрещено: инструкции по взлому, краже учёток, обходу капчи сторонними «решателями», обходу закона. Легитимные альтернативы: официальный API, другой публичный URL, поиск site:домен, ручной шаг пользователя. Язык ответа — как у поля goal в запросе (русский, если цель по-русски)."""


class TaskScoutModule:
    """Инструменты разведки задачи для brain (авто-регистрация в core.tools)."""

    async def scout_plan(
        self,
        goal: str,
        user_id: str = "unknown",
        urls: str = "",
        constraints: str = "",
    ) -> Dict[str, Any]:
        if not scout_enabled():
            return {"error": "TaskScout disabled (TASK_SCOUT_ENABLED=false)", "skipped": True}
        goal = (goal or "").strip()
        if not goal:
            return {"error": "goal is required", "hint": "Передай формулировку задачи в поле goal."}

        mem_blob = ""
        try:
            from core.brain.runtime import _memory

            mem_items = await _memory.on_before_response(
                str(user_id), f"task scout plan: {goal} {urls}"[:4000]
            )
            mem_blob = _memories_blob(mem_items if isinstance(mem_items, list) else [])
        except Exception as e:
            logger.debug("TaskScout mem0: %s", e)

        hosts = _hosts_from_urls_blob(urls)
        domain_guess = hosts[0] if hosts else ""
        playbooks = _recall_playbooks_text(domain_guess, goal, limit=6)
        if not playbooks and hosts:
            playbooks = _recall_playbooks_text("", goal, limit=4)

        user_block = (
            f"Цель:\n{goal}\n\n"
            f"URL (если есть):\n{(urls or '').strip()}\n\n"
            f"Ограничения/контекст:\n{(constraints or '').strip()}\n\n"
            f"Фрагменты из памяти (Mem0):\n{mem_blob or '(пусто)'}\n\n"
            f"Сохранённые заметки по теме:\n{playbooks or '(нет)'}\n"
        )

        llm = get_openrouter_provider()
        try:
            max_tok = int((os.getenv("TASK_SCOUT_MAX_TOKENS") or "1600").strip() or "1600")
        except ValueError:
            max_tok = 1600
        max_tok = max(400, min(max_tok, 4000))

        out = await llm.generate(
            user_block,
            system_prompt=_SCOUT_SYSTEM,
            max_tokens=max_tok,
            temperature=0.25,
            telemetry_tag="task_scout_plan",
            telemetry_kind="task_scout",
        )
        if out.get("error"):
            return {
                "error": out.get("error"),
                "content": (out.get("content") or "")[:2000],
                "mem0_snippet_chars": len(mem_blob),
                "playbooks_chars": len(playbooks),
            }

        content = (out.get("content") or "").strip()
        plan = _parse_json_object(content)
        if not plan:
            return {
                "parse_error": True,
                "raw": content[:4000],
                "mem0_snippet_chars": len(mem_blob),
                "playbooks_chars": len(playbooks),
                "hint": "Модель вернула не-JSON; используй raw как черновик или повтори вызов.",
            }

        plan["meta"] = {
            "mem0_snippet_chars": len(mem_blob),
            "playbooks_chars": len(playbooks),
            "domains_guessed": hosts[:5],
        }
        return plan

    async def save_playbook_note(
        self,
        domain: str,
        title: str,
        body: str,
        user_id: str = "unknown",
        tags: str = "",
    ) -> Dict[str, Any]:
        if not scout_enabled():
            return {"error": "TaskScout disabled", "skipped": True}
        from core.utils.llm_sanitize import sanitize_llm_value
        body = sanitize_llm_value(body)
        domain = (domain or "").strip().lower()[:200]
        title = (title or "").strip()[:200]
        if not title or not body:
            return {"error": "title and body are required"}

        path = playbook_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except OSError:
            pass

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "domain": domain or "general",
            "title": title,
            "body": body[:12000],
            "tags": (tags or "").strip()[:500],
            "user_id": str(user_id)[:64],
        }
        try:
            with _SCOUT_LOCK:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        except OSError as e:
            return {"error": str(e), "path": path}

        try:
            max_lines = int((os.getenv("TASK_SCOUT_PLAYBOOK_MAX_LINES") or "2000").strip() or "2000")
            if max_lines > 0 and os.path.isfile(path) and os.path.getsize(path) > 2_000_000:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                if len(lines) > max_lines:
                    with open(path, "w", encoding="utf-8") as f:
                        f.writelines(lines[-max_lines:])
        except (OSError, ValueError):
            pass

        return {"ok": True, "path": path, "domain": rec["domain"], "title": title}

    async def recall_playbooks(
        self,
        domain: str = "",
        query: str = "",
        limit: int = 8,
        user_id: str = "unknown",
    ) -> Dict[str, Any]:
        _ = user_id
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 8
        lim = max(1, min(lim, 24))
        text = _recall_playbooks_text(domain, query, lim)
        if not text:
            return {"items": [], "hint": "Заметок не найдено; сохраняй через TaskScout.save_playbook_note."}
        parts = text.split("\n---\n")
        return {"items": parts[:lim], "count": len(parts)}
