"""
Единый слой доменных подсказок для мозга: не отдельные костыли, а приоритетный маршрут
«какой класс задачи → какие семейства tools в первую очередь».

Не заменяет оркестратор/intent и не вызывает LLM — только компактная строка в промпт.
Отключить: GOAL_DOMAIN_POLICY_ENABLED=false
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class _Policy:
    key: str
    priority: int  # меньше — раньше проверяется
    patterns: Tuple[re.Pattern, ...]
    hint: str


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.IGNORECASE)


_POLICIES: Tuple[_Policy, ...] = (
    _Policy(
        key="legal",
        priority=10,
        patterns=(
            _rx(
                r"законодательств",
                r"\bзакон(а|у|е|ом|ы|ов|ам|ах|ами)?\b",
                r"\bнпа\b",
                r"кодекс",
                r"\bуказ\b",
                r"\bпостановлени",
                r"статья\s+\d+",
                r"ответственност",
                r"общ\w*\s+баз\w*\s+документ",
                r"локаль\w*\s+баз\w*",
            ),
        ),
        hint=(
            "Домен **право**: обзорные формулировки — сначала **UniversalSearch** / **UrlFetch**; "
            "локальный корпус уже загруженных актов — **DocumentCorpus.unified_search** / **list_catalog**; "
            "не выдумывай URL и не смешивай с архивом заметок пользователя."
        ),
    ),
    _Policy(
        key="education",
        priority=20,
        patterns=(
            _rx(
                r"учебник",
                r"пособие",
                r"электронн\w*\s+учебник",
                r"книг\w*\s+по\s+",
                r"гдз",
            ),
        ),
        hint=(
            "Домен **учебники**: **BooksRAG.** (search_book → resolve_book) если в tools; "
            "иначе **UniversalSearch** + **UrlFetch**; PDF — **/filefrom** с URL из ответа инструмента."
        ),
    ),
    _Policy(
        key="books_rag",
        priority=30,
        patterns=(
            _rx(
                r"\bbooksrag\b",
                r"books_rag",
                r"книжн\w*\s+rag",
                r"rag\s+книг",
                r"семантическ\w*\s+поиск\s+по\s+книг",
                r"корпус\s+книг",
                r"индекс\s+учебник",  # локальный корпус, не портал
            ),
        ),
        hint=(
            "Домен **книжный RAG (локальный корпус)**: инструменты **BooksRAG.** из списка tools; "
            "не путать с **UserKnowledgeArchive** (заметки/вложения)."
        ),
    ),
    _Policy(
        key="user_memory",
        priority=40,
        patterns=(
            _rx(
                r"архив\s+знан",
                r"архив\w*\s+замет",
                r"заметок\s+и\s+что",
                r"user\s*knowledge",
                r"личн\w*\s+библиотек",
                r"личн\w*\s+баз",
                r"что\s+сохранен",
                r"что\s+в\s+общей\s+базе",
                r"вложен",
                r"personal_library",
                r"knowledge_archive",
                r"/mem_remember",
                r"mem0",
                r"мои\s+факты",
            ),
        ),
        hint=(
            "Домен **память пользователя (локально)**: заметки — **UserKnowledgeArchive.archive_***; "
            "файлы из вложений «Личное» — **personal_library_***; облако фактов — Mem0 / **/get_mem0_facts**; "
            "книжный корпус — отдельно **BooksRAG**."
        ),
    ),
    _Policy(
        key="schedule_rail",
        priority=50,
        patterns=(
            _rx(
                r"электричк",
                r"пригород",
                r"расписани\w*\s+поезд",
                r"suburban",
            ),
        ),
        hint=(
            "Домен **расписание пригорода**: **Schedule.suburban_rail_schedule_links** (origin/destination); "
            "не путать с персональным **Schedule.get_schedule**."
        ),
    ),
    _Policy(
        key="admin_diag",
        priority=60,
        patterns=(
            _rx(
                r"/admin_health",
                r"/admin_connectivity",
                r"/admin_diagnostic",
                r"/admin_bug",
                r"admin_health",
                r"bundle\.json",
                r"zip_read",
                r"диагностик\w*\s+бот",
                r"логи\s+бот",
            ),
        ),
        hint=(
            "Домен **диагностика/админ**: slash **/admin_*** и **RuntimeDiagnostic.** / чтение ZIP **/zip_read**; "
            "не выдумывать баланс API — у админа **/admin_connectivity**, **/admin_llm_usage**."
        ),
    ),
    _Policy(
        key="web_fact",
        priority=70,
        patterns=(
            _rx(
                r"https?://",
                r"найди\s+в\s+интернет",
                r"погугли",
                r"что\s+такое\s+\w",
                r"кто\s+такой",
                r"\bwiki",
                r"wikipedia",
            ),
        ),
        hint=(
            "Домен **веб-факты**: цепочка **UrlFetch** → **UniversalSearch** → **Wikipedia** / **SiteRecipe** по правилам промпта; "
            "не подменять правовым корпусом без запроса про законодательство."
        ),
    ),
)


def route_goal_domain(user_text: str) -> Optional[Tuple[str, str]]:
    """
    Возвращает (domain_key, hint) для первой сработавшей политики или None = general.
    """
    if not _truthy("GOAL_DOMAIN_POLICY_ENABLED", default=True):
        return None
    raw = (user_text or "").strip()
    if not raw:
        return None
    matched: List[Tuple[int, str, str]] = []
    for pol in _POLICIES:
        for pat in pol.patterns:
            if pat.search(raw):
                matched.append((pol.priority, pol.key, pol.hint))
                break
    if not matched:
        return None
    matched.sort(key=lambda x: x[0])
    _pri, key, hint = matched[0]
    return key, hint


def _goal_runner_on_for_hints() -> bool:
    ex = (os.getenv("GOAL_RUNNER_EXECUTOR_MODE") or os.getenv("GOAL_RUNNER_ULTIMATE") or "").strip().lower()
    if ex in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("GOAL_RUNNER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _multistep_boundary_addon_enabled() -> bool:
    raw = os.getenv("GOAL_DOMAIN_MULTISTEP_HINT")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def format_domain_routing_addon(user_text: str) -> str:
    """Строки для подмешивания в tool_routing_hint: домен + опционально граница чат vs Goal Runner."""
    parts: List[str] = []
    r = route_goal_domain(user_text)
    if r:
        key, hint = r
        parts.append(f"goal_domain_policy: [{key}] {hint}")
    if _multistep_boundary_addon_enabled() and _goal_runner_on_for_hints():
        try:
            from core.brain.goal_runner_nudge import warrants_multistep_goal_text
        except Exception:
            warrants_multistep_goal_text = lambda _t: False
        raw = (user_text or "").strip()
        if raw and warrants_multistep_goal_text(raw):
            parts.append(
                "goal_runner_boundary: многошаговая формулировка — возможна **дорожка исполнителя** (Goal Runner, автостарт); "
                "если ядро уже ведёт пошаговый план, не пытайся закрыть всю цепочку одним ответом в **дорожке чата**."
            )
    return "\n".join(parts).strip()
