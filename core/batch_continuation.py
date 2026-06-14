"""
Batch Continuation — запоминает неотвеченные пункты из batch-запроса.

Если пользователь отправил 50 вопросов, а бот ответил на первые 5,
batch_continuation сохраняет пункты 6-50 в routing_prefs.
На следующее "продолжи" — подмешивает их в external_hint.

Жизненный цикл:
  1. extract_items(text)       — разобрать батч на пункты
  2. compute_pending(items, assistant_text) — вычислить неотвеченные
  3. store_pending(rec, items)  — сохранить в routing_prefs
  4. build_continuation_hint(rec, user_text) — собрать hint для "продолжи"
  5. clear_pending(rec)         — очистить когда батч завершён
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.regex_safe import cap_regex_input, safe_re_findall, safe_re_match, safe_re_search, safe_re_split, safe_re_sub

from core.brain.router_classifier import _detect_batch

logger = logging.getLogger(__name__)

# -- Конфигурация --
_BATCH_MAX_ITEMS = int(os.getenv("BATCH_MAX_ITEMS", "200"))  # макс. пунктов в одном батче
_BATCH_MAX_HINT_CHARS = int(os.getenv("BATCH_MAX_HINT_CHARS", "3000"))  # макс. длина hint'а


# =====================================================================
# Парсинг батча на отдельные пункты
# =====================================================================

_NUMBERED_LINE_RE = re.compile(r"^\s*\d+[\.\)]\s*(.+)$")
_INSTRUCTION_PREFIXES = (
    "ответь", "напиши", "сделай", "отвечай", "перечисли",
    "дай ответ", "выполни", "список",
)


def _is_instruction_line(line: str) -> bool:
    low = (line or "").strip().lower()
    return any(low.startswith(p) for p in _INSTRUCTION_PREFIXES)


def _looks_like_independent_question(line: str) -> bool:
    """Строка похожа на отдельный вопрос/запрос (не преамбула к одной задаче)."""
    s = (line or "").strip()
    if not s:
        return False
    if "?" in s:
        return True
    low = s.lower()
    return low.startswith(
        ("почему", "как ", "что ", "зачем", "когда", "где ", "сколько", "переведи", "посчитай", "напиши")
    )


def is_unified_problem(text: str) -> bool:
    """Одна связная задача с нумерованными подпунктами — не независимый batch-список."""
    txt = (text or "").strip()
    if not txt:
        return False
    lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
    if len(lines) < 3 or len(lines) > 14:
        return False

    numbered: List[str] = []
    other: List[str] = []
    for line in lines:
        if safe_re_match(_NUMBERED_LINE_RE, line, max_len=512):
            numbered.append(line)
        elif not line.endswith(":") and not _is_instruction_line(line):
            other.append(line)

    if len(numbered) < 2:
        return False

    independent = sum(1 for o in other if _looks_like_independent_question(o))
    if independent >= 3:
        return False

    narrative = any(len(o) > 35 and "?" not in o for o in other)
    return narrative or len(numbered) <= 6


_UNIFIED_MATH_RE = re.compile(
    r"(?i)(тессеракт|пентеракт|гиперкуб|"
    r"\d+\s*[-‑]?\s*мерн|"
    r"четыр[её]хмерн|тр[её]хмерн\s+гран|"
    r"гипергран|n\s*[-‑]?\s*мерн|"
    r"сколько\s+всего\s+.{0,120}?\s+ячеек)"
)


def looks_like_unified_math_problem(text: str) -> bool:
    """Единая геометрическая/комбинаторная задача (тессеракт, пентеракт, …)."""
    if not is_unified_problem(text):
        return False
    return bool(safe_re_search(_UNIFIED_MATH_RE, text, max_len=2048))


def resolve_unified_problem_profile(text: str) -> str:
    """
    Профиль для единой задачи с нумерованными подпунктами.
    Раньше использовался несуществующий «reasoning» → fallback на standard и обрезание ответа.
    """
    if looks_like_unified_math_problem(text):
        return "math_solve"
    return "batch"


def _extract_numbered_with_preamble(lines: List[str]) -> List[str]:
    """Подпункты 1./2./… + общая преамбула; преамбулу приклеиваем к каждому вопросу."""
    preamble_parts: List[str] = []
    numbered: List[tuple[int, str]] = []
    for line in lines:
        m = safe_re_match(_NUMBERED_LINE_RE, line, max_len=512)
        if m:
            num_m = re.match(r"^\s*(\d+)", line)
            num = int(num_m.group(1)) if num_m else len(numbered) + 1
            numbered.append((num, m.group(1).strip()))
        elif not line.endswith(":") and not _is_instruction_line(line):
            preamble_parts.append(line)

    if len(numbered) < 2:
        return []

    independent = sum(1 for p in preamble_parts if _looks_like_independent_question(p))
    if independent >= 3:
        return []

    preamble = "\n".join(preamble_parts).strip()
    ordered = [q for _, q in sorted(numbered, key=lambda x: x[0])]
    if preamble:
        return [f"{preamble}\n\n{q}" for q in ordered]
    return ordered


def extract_items(text: str) -> List[str]:
    """Разобрать текст на отдельные пункты.

    Поддерживаемые форматы:
      - Многострочный: каждая строка = пункт
      - Нумерованный список: 1. xxx 2. yyy
      - Список через запятую: xxx, yyy, zzz

    Возвращает список строк (очищенных, непустых).
    """
    txt = (text or "").strip()
    if not txt:
        return []

    lines = [l.strip() for l in txt.split("\n") if l.strip()]

    # 0. Нумерованные подпункты одной задачи (тессеракт, тест с преамбулой)
    numbered_items = _extract_numbered_with_preamble(lines)
    if numbered_items:
        return numbered_items[: _BATCH_MAX_ITEMS]

    items: List[str] = []

    # 1. Многострочный (приоритет)
    if len(lines) >= 4:
        # Отфильтровать строки-инструкции и заголовки (заканчиваются на ":")
        items = [l for l in lines if not l.endswith(":") and not _is_instruction_line(l)]
        if not items or len(items) < 3:
            items = lines  # fallback: если всё отфильтровалось, берём сырые строки
    elif len(lines) >= 2:
        items = [l for l in lines if not l.endswith(":") and not _is_instruction_line(l)]
        if not items or len(items) < 2:
            items = lines
    else:
        # 2. Одна строка: проверяем нумерованный список
        numbered = safe_re_findall(
            r'(?:^|\s)\d+[\.\)]\s*(.*?)(?=\s+\d+[\.\)]\s|$)', txt, max_len=2048
        )
        if len(numbered) >= 3:
            items = [s.strip() for s in numbered if s.strip()]
        else:
            # 3. Список через запятую / точку с запятой
            sep_count = txt.count(",") + txt.count(";")
            if sep_count >= 4:
                parts = re.split(r'[,;]+', txt)
                items = [s.strip() for s in parts if s.strip() and len(s.strip()) > 2]

    # Удаляем нумерацию из начала каждого пункта (1. 2. 1) 2))
    cleaned: List[str] = []
    for item in items:
        item = re.sub(r'^\d+[\.\)]\s*', '', item).strip()
        if item:
            cleaned.append(item)

    return cleaned[: _BATCH_MAX_ITEMS]


# =====================================================================
# Определение неотвеченных пунктов
# =====================================================================

def compute_pending(items: List[str], assistant_text: str) -> List[str]:
    """Определить, какие пункты не были отвечены в assistant_text.

    Алгоритм:
      1. Ищем в assistant_text номера пунктов (1. 2. ... N.)
      2. Если найден последний номер — всё что после него = pending
      3. Если номеров нет — считаем что отвечено примерно половину
         (консервативно: половина, т.к. мы не знаем точного прогресса)
    """
    if not items or not assistant_text:
        return []

    # Ищем номера в ответе ассистента (только в диапазоне [1, len(items)]
    all_nums = [int(m) for m in re.findall(r'\b(\d+)[\.\)]\s', assistant_text) if m.isdigit()]
    nums_in_reply = [n for n in all_nums if 1 <= n <= len(items)]
    if nums_in_reply:
        last_answered = max(nums_in_reply)
        if last_answered < len(items):
            return items[last_answered:]
        else:
            return []  # всё отвечено

    # Нет нумерации — эвристика: отвечено половина
    # (лучше вернуть половину, чем ничего, чтобы "продолжи" работал)
    if len(items) <= 5:
        return []  # мало пунктов — считаем что всё отвечено

    # Проверяем: есть ли в assistant_text текст, похожий на первые пункты
    # Если да — считаем что первые N отвечены
    overlap = _compute_overlap(items, assistant_text)
    if overlap > 0:
        return items[overlap:]

    # Консервативно: считаем что отвечено половину
    mid = len(items) // 2
    return items[mid:]


def _compute_overlap(items: List[str], assistant_text: str) -> int:
    """Сколько пунктов из начала списка перекрываются с assistant_text."""
    low = assistant_text.lower()
    overlap = 0
    for item in items:
        # Берём первые 30 символов пункта — достаточно для проверки
        sig = item[:30].strip().lower()
        if not sig or len(sig) < 5:
            overlap += 1
            continue
        if sig in low:
            overlap += 1
        else:
            break
    return overlap


# =====================================================================
# Хранение в routing_prefs
# =====================================================================

PENDING_KEY = "batch_pending_items"
ORIGINAL_KEY = "batch_original_items"


def _items_look_like_batch(items: List[str]) -> bool:
    """Отсечь ложный batch: длинные дубликаты (отчёт о баге, статья)."""
    if not items or len(items) < 2:
        return bool(items)
    lengths = [len(i) for i in items]
    if max(lengths) > 600:
        return False
    if len(set(i[:80] for i in items)) == 1 and lengths[0] > 200:
        return False
    return True


def store_pending(rec: Dict[str, Any], items: List[str]) -> None:
    """Сохранить неотвеченные пункты в routing_prefs."""
    if not items:
        return
    if not _items_look_like_batch(items):
        logger.info(
            "[batch_continuation] skip store: items look like prose/report, not a batch (%d items)",
            len(items),
        )
        return
    rp = rec.get("routing_prefs") or {}
    if not isinstance(rp, dict):
        rp = {}
    rp[PENDING_KEY] = items[: _BATCH_MAX_ITEMS]
    rec["routing_prefs"] = rp
    logger.debug("[batch_continuation] stored %d pending items", len(items))


def store_original(rec: Dict[str, Any], items: List[str]) -> None:
    """Сохранить исходные пункты батча (чтобы "продолжи" знал формат)."""
    if not items:
        return
    rp = rec.get("routing_prefs") or {}
    if not isinstance(rp, dict):
        rp = {}
    rp[ORIGINAL_KEY] = items[: _BATCH_MAX_ITEMS]
    rec["routing_prefs"] = rp


def get_pending(rec: Dict[str, Any]) -> List[str]:
    """Вернуть неотвеченные пункты из routing_prefs."""
    rp = rec.get("routing_prefs") or {}
    if not isinstance(rp, dict):
        return []
    items = rp.get(PENDING_KEY, [])
    if isinstance(items, list):
        return [str(i) for i in items if str(i).strip()]
    return []


def get_original(rec: Dict[str, Any]) -> List[str]:
    """Вернуть исходные пункты батча."""
    rp = rec.get("routing_prefs") or {}
    if not isinstance(rp, dict):
        return []
    items = rp.get(ORIGINAL_KEY, [])
    if isinstance(items, list):
        return [str(i) for i in items if str(i).strip()]
    return []


def clear_pending(rec: Dict[str, Any]) -> None:
    """Очистить неотвеченные пункты."""
    rp = rec.get("routing_prefs") or {}
    if isinstance(rp, dict):
        rp.pop(PENDING_KEY, None)
        rp.pop(ORIGINAL_KEY, None)


def has_pending(rec: Dict[str, Any]) -> bool:
    """Есть ли неотвеченные пункты."""
    return bool(get_pending(rec))


# =====================================================================
# Формирование external_hint для продолжения батча
# =====================================================================

def build_continuation_hint(rec: Dict[str, Any], user_text: str) -> str:
    """Собрать hint для external_hint, если пользователь просит продолжить.

    Определяет континуацию (продолжи, дальше, ещё), подхватывает
    неотвеченные пункты.

    Возвращает пустую строку, если континуация не обнаружена.
    """
    low = (user_text or "").strip().lower()
    if not low:
        return ""

    # Триггеры континуации
    _continue_triggers = {
        "продолжи", "продолжай", "дальше", "ещё", "еще",
        "далее", "continue", "more", "next", "давай дальше",
    }
    if low not in _continue_triggers and not any(t in low for t in _continue_triggers):
        return ""

    pending = get_pending(rec)
    original = get_original(rec)
    if not pending:
        return ""

    # Собираем hint
    total = len(original) if original else "?"
    answered = (total if isinstance(total, int) else 0) - len(pending)
    hint_lines = [
        f"BATCH_CONTINUATION: пользователь просит продолжить список из {total} пунктов. "
        f"Отвечено: {answered}. Осталось: {len(pending)}.",
        "Ответь на каждый оставшийся пункт по порядку, нумеруя продолжая предыдущую нумерацию.",
        f"Начни с пункта №{answered + 1}." if isinstance(answered, int) else "",
        "",
        "Неотвеченные пункты:",
    ]
    for i, item in enumerate(pending, start=(answered + 1 if isinstance(answered, int) else 1)):
        line = f"{i}. {item}"
        hint_lines.append(line)

    hint = "\n".join(hint_lines)
    if len(hint) > _BATCH_MAX_HINT_CHARS:
        hint = hint[: _BATCH_MAX_HINT_CHARS] + "\n..."

    return hint


# =====================================================================
# API для pipeline
# =====================================================================

PROFILE_BATCH = "batch"


def is_continuation(text: str) -> bool:
    """Проверить, является ли текст запросом на продолжение."""
    low = (text or "").strip().lower()
    _continue_triggers = {"продолжи", "продолжай", "дальше", "ещё", "еще",
                          "далее", "continue", "more", "next", "давай дальше"}
    return low in _continue_triggers or any(t in low for t in _continue_triggers)


def handle_batch_continuation(
    rec: Dict[str, Any],
    user_text: str,
    assistant_text: str,
) -> List[str]:
    """Обработать батч после ответа: обновить неотвеченные пункты.

    Вызывается из behavior_store.update_session() после записи хода.
    Работает и на батч-ходу (сохраняет pending), и на континуации
    (обновляет pending, сверяясь с оригиналом).

    Args:
        rec: record из BehaviorStore
        user_text: текст пользователя (оригинальный батч)
        assistant_text: ответ ассистента

    Returns:
        список всё ещё неотвеченных пунктов (пустой = всё отвечено)
    """
    if not rec or not user_text or not assistant_text:
        return []

    # Fast path: если это не batch и нет сохранённых pending — нечего делать
    if not _detect_batch(user_text) and not get_pending(rec):
        return []

    # Обычное сообщение при «зависшем» batch — сбрасываем, иначе каждый ответ
    # (анекдот, «какой день») снова режет pending пополам и портит контекст.
    if not _detect_batch(user_text) and not is_continuation(user_text):
        if get_pending(rec):
            clear_pending(rec)
            logger.info(
                "[batch_continuation] cleared stale pending (unrelated message, not a batch/continue)"
            )
        return []

    # Определяем исходные пункты: из текущего текста (если батч) или из сохранённых
    if _detect_batch(user_text):
        items = extract_items(user_text)
        original = items
    else:
        # Это континуация — берём сохранённый оригинал
        original = get_original(rec)
        items = get_pending(rec)

    if not items:
        return []

    # Считаем новые pending на основе ответа ассистента
    pending = compute_pending(original, assistant_text)

    if pending:
        store_pending(rec, pending)
        store_original(rec, original)
        logger.info(
            "[batch_continuation] %d/%d items pending for continuation",
            len(pending), len(original),
        )
    else:
        clear_pending(rec)
        logger.info("[batch_continuation] all %d items answered, cleared", len(original))

    return pending
