"""
Якорь для «перепроверь / посмотри ещё раз» — ответ на последний вопрос, не на старую тему.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_RECHECK_RE = re.compile(
    r"(?ui)"
    r"(?:"
    r"(?:может\s+(?:ты|вы)\s+)?(?:хорошо\s+|ещё\s+|еще\s+)?(?:посмотри|посмотр|пересмотри|перечитай|проверь)(?:шь|ите|ь)?"
    r"|(?:ещё|еще)\s+раз\s+(?:посмотри|проверь|перечитай)"
    r"|look\s+again|check\s+again|read\s+again"
    r")"
)

# Не путать с «посмотри назад по переписке» (dialog recall).
_DIALOG_RECALL_RE = re.compile(
    r"(?ui)(?:посмотри\s+назад|проверь\s+переписк|найди\s+в\s+истори)"
)


def looks_like_recheck_last_answer(text: Any) -> bool:
    s = (text or "").strip()
    if len(s) < 6 or len(s) > 120:
        return False
    if _DIALOG_RECALL_RE.search(s):
        return False
    return bool(_RECHECK_RE.search(s))


def _row_text(row: Dict[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or "").strip()


def _row_role(row: Dict[str, Any]) -> str:
    return str(row.get("role") or row.get("author") or "").lower()


def last_substantive_user_question(
    recent_dialogue: Any,
    *,
    skip_current: bool = True,
    min_len: int = 8,
) -> Optional[str]:
    """Последний содержательный вопрос пользователя (не текущая реплика-recheck)."""
    if not isinstance(recent_dialogue, list):
        return None
    rows: List[Dict[str, Any]] = [r for r in recent_dialogue if isinstance(r, dict)]
    if skip_current and rows:
        rows = rows[:-1]
    for row in reversed(rows):
        if _row_role(row) not in ("user", "human", ""):
            continue
        t = _row_text(row)
        if len(t) < min_len:
            continue
        if looks_like_recheck_last_answer(t):
            continue
        return t
    return None


def last_qa_pair(recent_dialogue: Any) -> Optional[Tuple[str, str]]:
    """Пара (последний user, следующий assistant) перед текущим ходом."""
    if not isinstance(recent_dialogue, list) or len(recent_dialogue) < 2:
        return None
    rows = [r for r in recent_dialogue if isinstance(r, dict)]
    if len(rows) < 2:
        return None
    # Исключаем текущую реплику (последняя user в списке часто = текущий ход до append)
    end = len(rows) - 1
    if end > 0 and _row_role(rows[end]) in ("user", "human", ""):
        end -= 1
    user_q = ""
    asst_a = ""
    for i in range(end, -1, -1):
        role = _row_role(rows[i])
        txt = _row_text(rows[i])
        if not txt:
            continue
        if role in ("assistant", "bot", "ai") and not asst_a:
            asst_a = txt
            continue
        if role in ("user", "human", "") and asst_a and not user_q:
            if looks_like_recheck_last_answer(txt):
                continue
            user_q = txt
            break
    if user_q and asst_a:
        return user_q, asst_a
    return None


def build_recheck_anchor_hint(user_text: str, recent_dialogue: Any) -> str:
    if not looks_like_recheck_last_answer(user_text):
        return ""
    pair = last_qa_pair(recent_dialogue)
    last_q = last_substantive_user_question(recent_dialogue, skip_current=True)
    parts: List[str] = [
        "(Перепроверка: пользователь просит пересмотреть ПОСЛЕДНИЙ ответ, "
        "не возвращаться к более ранней теме из истории.)"
    ]
    if last_q:
        parts.append(f"Приоритетный вопрос для ответа: «{last_q[:320]}».")
    if pair:
        u, a = pair
        parts.append(
            f"Последняя пара в диалоге — вопрос: «{u[:200]}»; твой ответ был: «{a[:200]}». "
            "Если пользователь сомневается — пересчитай/перепроверь именно этот вопрос."
        )
    return " ".join(parts)
