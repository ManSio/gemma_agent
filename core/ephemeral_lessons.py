"""
Временные «латки» без деплоя: триггер в тексте пользователя → подсказка в мозг и опционально general вместо math.

Файл: data/runtime/ephemeral_lessons.json
Переопределение: EPHEMERAL_LESSONS_PATH
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_lock = threading.Lock()

logger = logging.getLogger(__name__)

_MAX_LESSONS = 200
# Слишком короткие триггеры в режиме contains дают массу ложных срабатываний (autolearn «+», «да»).
_MIN_CONTAINS_TRIGGER_LEN = 3


def _repo_root() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path(__file__).resolve().parent.parent


def _default_path() -> Path:
    raw = (os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _repo_root() / p
    return (p / "ephemeral_lessons.json").resolve()


def lessons_path() -> Path:
    env = (os.getenv("EPHEMERAL_LESSONS_PATH") or "").strip()
    if env:
        pp = Path(env)
        return pp.resolve() if pp.is_absolute() else (_repo_root() / pp).resolve()
    return _default_path()


def _empty_doc() -> Dict[str, Any]:
    return {"version": 1, "lessons": []}


def load_document() -> Dict[str, Any]:
    path = lessons_path()
    if not path.is_file():
        return _empty_doc()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_doc()
        lessons = data.get("lessons")
        if not isinstance(lessons, list):
            data["lessons"] = []
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("ephemeral_lessons: cannot load %s: %s", path, e)
        return _empty_doc()


def _save_document(doc: Dict[str, Any]) -> None:
    path = lessons_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def snapshot_for_operator() -> Dict[str, Any]:
    path = lessons_path()
    doc = load_document()
    lessons = [x for x in (doc.get("lessons") or []) if isinstance(x, dict) and x.get("active", True)]
    return {
        "path": str(path),
        "exists": path.is_file(),
        "active_count": len(lessons),
        "total_count": len(doc.get("lessons") or []),
    }


def _lesson_key(trigger: str, instruction: str, match_regex: bool, force_general: bool) -> str:
    return json.dumps(
        [trigger.strip(), instruction.strip(), match_regex, force_general],
        ensure_ascii=False,
        sort_keys=True,
    )


def _trim(doc: Dict[str, Any]) -> None:
    lessons: List[Dict[str, Any]] = [x for x in doc.get("lessons") or [] if isinstance(x, dict)]
    if len(lessons) <= _MAX_LESSONS:
        doc["lessons"] = lessons
        return
    lessons.sort(key=lambda x: float(x.get("created_ts") or 0.0))
    doc["lessons"] = lessons[-_MAX_LESSONS:]


def add_lesson(
    trigger: str,
    instruction: str,
    *,
    match_regex: bool = False,
    force_general_when_math_probe: bool = False,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trig = (trigger or "").strip()
    inst = (instruction or "").strip()
    if not trig or not inst:
        raise ValueError("trigger и instruction не должны быть пустыми")
    if not match_regex and len(trig) < _MIN_CONTAINS_TRIGGER_LEN:
        raise ValueError(
            f"триггер для contains не короче {_MIN_CONTAINS_TRIGGER_LEN} символов "
            "(или используйте regex:... для узкого совпадения)"
        )
    if match_regex:
        try:
            re.compile(trig)
        except re.error as e:
            raise ValueError(f"невалидный regex: {e}") from e
    key = _lesson_key(trig, inst, match_regex, force_general_when_math_probe)
    with _lock:
        doc = load_document()
        lessons: List[Dict[str, Any]] = [x for x in doc.get("lessons") or [] if isinstance(x, dict)]
        for le in lessons:
            if le.get("dedupe_key") == key:
                le["hit_count"] = int(le.get("hit_count") or 0) + 1
                le["updated_ts"] = time.time()
                doc["lessons"] = lessons
                _save_document(doc)
                return le
        row = {
            "id": secrets.token_hex(6),
            "trigger": trig,
            "instruction": inst,
            "match": "regex" if match_regex else "contains",
            "force_general_when_math_probe": bool(force_general_when_math_probe),
            "active": True,
            "created_ts": time.time(),
            "updated_ts": time.time(),
            "hit_count": 1,
            "dedupe_key": key,
        }
        if isinstance(meta, dict) and meta:
            row["meta"] = dict(meta)
        lessons.append(row)
        doc["lessons"] = lessons
        _trim(doc)
        _save_document(doc)
        return row


def deactivate_all_lessons() -> int:
    """Отключить все активные латки. Возвращает число отключённых."""
    with _lock:
        doc = load_document()
        n = 0
        for le in doc.get("lessons") or []:
            if isinstance(le, dict) and le.get("active", True):
                le["active"] = False
                le["updated_ts"] = time.time()
                n += 1
        if n:
            _save_document(doc)
        return n


def deactivate_lesson(lesson_id: str) -> bool:
    lid = (lesson_id or "").strip()
    if not lid:
        return False
    with _lock:
        doc = load_document()
        changed = False
        for le in doc.get("lessons") or []:
            if isinstance(le, dict) and le.get("id") == lid:
                le["active"] = False
                le["updated_ts"] = time.time()
                changed = True
                break
        if changed:
            _save_document(doc)
        return changed


def match_lessons(text: str) -> List[Dict[str, Any]]:
    raw = text or ""
    out: List[Dict[str, Any]] = []
    doc = load_document()
    for le in doc.get("lessons") or []:
        if not isinstance(le, dict) or not le.get("active", True):
            continue
        trig = str(le.get("trigger") or "")
        if not trig:
            continue
        mode = le.get("match") or "contains"
        try:
            if mode == "regex":
                if re.search(trig, raw, re.IGNORECASE | re.DOTALL):
                    out.append(le)
            else:
                if len(trig) < _MIN_CONTAINS_TRIGGER_LEN:
                    continue
                if trig.lower() in raw.lower():
                    out.append(le)
        except re.error:
            logger.warning("ephemeral_lessons: skip bad regex id=%s", le.get("id"))
    return out


def force_general_when_math_probe(text: str) -> bool:
    return any(bool(x.get("force_general_when_math_probe")) for x in match_lessons(text))


def brain_addon_for_text(text: str) -> str:
    ms = match_lessons(text)
    if not ms:
        return ""
    lines = []
    for le in ms:
        inst = str(le.get("instruction") or "").strip()
        if inst:
            lines.append(inst)
    if not lines:
        return ""
    return "Временные правки оператора (до правки кода; строго соблюдай):\n" + "\n".join(
        f"- {ln}" for ln in lines
    )


def parse_remember_patch(rest: str) -> Tuple[str, str, bool, bool]:
    """
    Формат: триггер || инструкция [ || force_general ]
    Триггер может начинаться с regex: для режима регулярного выражения.
    """
    s = (rest or "").strip()
    if not s:
        raise ValueError("пусто после команды")
    parts = [p.strip() for p in s.split("||")]
    if len(parts) < 2:
        raise ValueError("формат: триггер || инструкция [ || force_general ]")
    trigger = parts[0]
    instruction = parts[1]
    flags = " ".join(parts[2:]).lower() if len(parts) > 2 else ""
    force_general = any(
        x in flags for x in ("force_general", "fg", "math_general", "no_math", "1", "true", "да")
    )
    match_regex = False
    if trigger.lower().startswith("regex:"):
        match_regex = True
        trigger = trigger[6:].strip()
    return trigger, instruction, match_regex, force_general


def export_for_cursor() -> Dict[str, Any]:
    from core.operator_rules import load_operator_rules, rules_path as operator_rules_path

    doc = load_document()
    op_path = str(operator_rules_path())
    op_rules = load_operator_rules()
    lessons = [x for x in (doc.get("lessons") or []) if isinstance(x, dict) and x.get("active", True)]
    md_lines = [
        "## Временные латки (ephemeral_lessons)",
        "",
        "Перенеси в постоянный код / `operator_rules.json` и удали из `ephemeral_lessons.json`.",
        "",
    ]
    for i, le in enumerate(lessons, 1):
        md_lines.append(f"### {i}. `{le.get('id')}`")
        md_lines.append(f"- **match:** {le.get('match', 'contains')}")
        md_lines.append(f"- **trigger:** `{le.get('trigger')}`")
        md_lines.append(f"- **instruction:** {le.get('instruction')}")
        if le.get("force_general_when_math_probe"):
            md_lines.append("- **force_general_when_math_probe:** true (intent math → general)")
        md_lines.append("")
    md_lines.extend(
        [
            "## operator_rules.json",
            f"Путь: `{op_path}`",
            "",
            "```json",
            json.dumps(op_rules, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Подсказка для Cursor",
            "1. Найди место маршрутизации (orchestrator / intent_heuristics / модуль).",
            "2. Внеси поведение как постоянное правило или тест.",
            "3. Очисти `data/runtime/ephemeral_lessons.json` для сработавших записей.",
            "",
        ]
    )
    return {
        "ephemeral_lessons_path": str(lessons_path()),
        "ephemeral_lessons": doc,
        "operator_rules_path": op_path,
        "operator_rules": op_rules,
        "markdown_for_cursor": "\n".join(md_lines),
    }
