"""Сброс «режима краткого ответа» в recent_dialogue перед новым содержательным вопросом."""
from __future__ import annotations

import re
from typing import Any, Dict, List

_BRIEF_USER_RE = re.compile(
    r"(одним\s+словом|только\s*:\s*|коротко|лаконично|в\s+одно\s+слово|"
    r"ответь\s+одним|say\s+only|just\s+say)",
    re.IGNORECASE,
)
_EXPLAIN_Q_RE = re.compile(
    r"(?i)\b(почему|зачем|отчего|как\s+работает|что\s+такое|объясни|расскажи\s+почему)\b",
)
_TRIVIAL_ACK = frozenset({"ok", "ок", "да", "нет", "yes", "no", "ага", "угу", "ладно"})


def _msg_text(row: Dict[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or "").strip()


def _msg_role(row: Dict[str, Any]) -> str:
    return str(row.get("role") or row.get("speaker") or "").strip().lower()


def filter_recent_after_brief_trap(
    recent_dialogue: List[Dict[str, Any]],
    user_text: str,
) -> List[Dict[str, Any]]:
    """
    Если пользователь задал новый «почему/зачем…», убрать хвост диалога с просьбой
    «скажи только: ок» и односложным ответом — иначе модель повторяет «ок».
    """
    if not recent_dialogue or not _EXPLAIN_Q_RE.search(user_text or ""):
        return recent_dialogue
    if _BRIEF_USER_RE.search(user_text or ""):
        return recent_dialogue

    out = list(recent_dialogue)
    changed = True
    while changed and len(out) >= 2:
        changed = False
        tail = out[-2:]
        u, a = tail[0], tail[1]
        if _msg_role(u) not in ("user", "human") or _msg_role(a) not in (
            "assistant",
            "bot",
            "model",
        ):
            break
        ut = _msg_text(u)
        at = _msg_text(a).lower().rstrip(".,!?…")
        if _BRIEF_USER_RE.search(ut) and (
            at in _TRIVIAL_ACK or (len(at) <= 8 and at.replace(" ", "").isdigit())
        ):
            out = out[:-2]
            changed = True
    return out
