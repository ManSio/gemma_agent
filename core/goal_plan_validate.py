"""
Минимальный валидатор планов Goal Runner: структура, финальный answer, инструменты, циклы, note.
Выкл: GOAL_RUNNER_PLAN_VALIDATOR=false. Авто-добавление answer: GOAL_RUNNER_PLAN_AUTO_APPEND_ANSWER=true.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Set

_ERR_LAST_ANSWER = "последний шаг должен быть kind=answer"


def validator_enabled() -> bool:
    return os.getenv("GOAL_RUNNER_PLAN_VALIDATOR", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def auto_append_answer() -> bool:
    return os.getenv("GOAL_RUNNER_PLAN_AUTO_APPEND_ANSWER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _tool_sig(row: Dict[str, Any]) -> str:
    tool = str(row.get("tool") or "").strip()
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    try:
        return f"{tool}\0{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    except TypeError:
        return f"{tool}\0{repr(args)}"


def validate_goal_plan(plan: List[Dict[str, Any]], allowed_tools: frozenset) -> List[str]:
    errs: List[str] = []
    if not plan:
        return ["план пуст"]
    last = plan[-1]
    if str(last.get("kind") or "").lower() != "answer":
        errs.append(_ERR_LAST_ANSWER)
    seen: Set[str] = set()
    for i, row in enumerate(plan):
        if not isinstance(row, dict):
            errs.append(f"шаг {i + 1}: неверная структура")
            continue
        note = str(row.get("note") or "").strip()
        if not note:
            errs.append(f"шаг {i + 1}: пустой note (нет цели шага)")
        kind = str(row.get("kind") or "").lower()
        if kind == "tool":
            tn = str(row.get("tool") or "").strip()
            if not tn:
                errs.append(f"шаг {i + 1}: не указан инструмент")
            elif allowed_tools and tn not in allowed_tools:
                errs.append(f"шаг {i + 1}: инструмент «{tn}» не из каталога")
            if tn:
                sig = _tool_sig(row)
                if sig in seen:
                    errs.append(
                        f"шаг {i + 1}: повтор вызова с теми же args (риск цикла)"
                    )
                seen.add(sig)
        elif kind != "answer":
            errs.append(f"шаг {i + 1}: неизвестный kind «{kind}»")
    return errs


def try_append_final_answer(plan: List[Dict[str, Any]]) -> bool:
    if not plan:
        return False
    if str(plan[-1].get("kind") or "").lower() == "answer":
        return False
    nid = int(plan[-1].get("id", len(plan) - 1)) + 1
    plan.append(
        {
            "id": nid,
            "kind": "answer",
            "tool": "",
            "args": {},
            "note": "Сформировать итог для пользователя по собранным данным.",
            "status": "pending",
            "result_excerpt": "",
            "retries": 0,
        }
    )
    return True


def validate_with_optional_fix(
    plan: List[Dict[str, Any]],
    allowed: frozenset,
) -> List[str]:
    if not validator_enabled():
        return []
    errs = validate_goal_plan(plan, allowed)
    if not errs:
        return []
    only_last = len(errs) == 1 and _ERR_LAST_ANSWER in errs[0]
    if only_last and auto_append_answer():
        if try_append_final_answer(plan):
            return validate_goal_plan(plan, allowed)
    return errs
