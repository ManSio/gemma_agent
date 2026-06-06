"""
Один короткий вызов LLM: глубина задачи, подцели, предпочтение краткого vs развёрнутого ответа.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def outline_enabled() -> bool:
    """Для nested/deep и длинных запросов — один короткий вызов LLM до основного мозга."""
    return effective_bool("STRATEGY_LLM_OUTLINE_ENABLED", default=False)


def should_run_outline(user_text: str, task_tier: str) -> bool:
    if not outline_enabled():
        return False
    if effective_bool("STRATEGY_LLM_OUTLINE_ALWAYS", default=False):
        return True
    tier = (task_tier or "").strip()
    if tier in ("nested", "deep"):
        return True
    try:
        min_ch = max(200, int((os.getenv("STRATEGY_LLM_OUTLINE_MIN_CHARS") or "420").strip() or "420"))
    except ValueError:
        min_ch = 420
    return len((user_text or "").strip()) >= min_ch


def _parse_json_obj(raw: str) -> Dict[str, Any]:
    s = (raw or "").strip()
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(s[i : j + 1])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


async def fetch_llm_task_outline(
    *,
    user_text: str,
    task_tier: str,
    dialogue_state: Dict[str, Any],
) -> Dict[str, Any]:
    if not outline_enabled():
        return {}
    from core.openrouter_provider import get_openrouter_provider

    prov = get_openrouter_provider()
    model = (os.getenv("OPENROUTER_MODEL_TASK_OUTLINE") or "").strip() or None
    try:
        max_tok = max(120, min(600, int((os.getenv("STRATEGY_LLM_OUTLINE_MAX_TOKENS") or "320").strip() or "320")))
    except ValueError:
        max_tok = 320
    text = (user_text or "").strip()[:3200]
    last_intent = str((dialogue_state or {}).get("last_intent") or "")
    sys = (
        "Ты классификатор задач и лёгкий сценарный аналитик. Отвечай ТОЛЬКО одним JSON-объектом, без markdown и без текста вокруг. "
        "Ключи: depth (строка single|multi), subgoals (массив строк, 0–5), prefer (строка short|thorough), notes (строка, можно пустая), "
        "scenarios (массив 0–4 объектов; каждый: branch — краткая ветка/условие, implication — следствие или риск; можно пустой массив). "
        "multi только если явно несколько вопросов, пронумерованные подзадачи или «сначала… потом». "
        "Если есть взаимоисключающие варианты, «что если», риски — заполни scenarios (даже при depth=single). "
        "Сообщения про ограничения банка, техработы, лимиты, валюту, снятие наличных, депозит отеля, оплату при "
        "заезде, поездку с риском остаться без денег — почти всегда depth=multi и непустой scenarios: ветки вроде "
        "«если снятие/переводы восстановятся» vs «если нет» с implication (действие и риск); для этих же тем "
        "ставь prefer=thorough, если пользователь явно не просит один короткий ответ в одну строку. "
        "Если пользователь резко меняет ситуацию (разрыв отношений, отмена прежнего сюжета) — depth=multi, "
        "в subgoals первым пунктом: зафиксировать новое состояние; не опираться на старую линию. "
        "Маркеры неопределённости (возможно, неизвестно, техработы, ограничения, срок не ясен, риск, «что если», "
        "давление срока «к отъезду», «через N дней») — сигнал к depth=multi, минимум два scenarios с implication как "
        "главный риск или ключевое действие; в notes кратко: цель, главные ограничения, что неизвестно, что уточнить; "
        "prefer=thorough, если пользователь не просит ультракраткий ответ."
    )
    user = (
        f"Эвристика уровня (подсказка): {task_tier}. Последний intent в диалоге: {last_intent}.\n\n"
        f"Сообщение пользователя:\n{text}"
    )
    try:
        out = await prov.generate(
            prompt=user,
            model=model,
            system_prompt=sys,
            max_tokens=max_tok,
            temperature=0.15,
            telemetry_tag="task_outline",
        )
    except Exception as e:
        logger.debug("task_outline llm: %s", e)
        return {}
    if out.get("error"):
        return {}
    obj = _parse_json_obj(str(out.get("content") or ""))
    depth = str(obj.get("depth") or "").strip().lower()
    if depth not in ("single", "multi"):
        depth = "single"
    prefer = str(obj.get("prefer") or "").strip().lower()
    if prefer not in ("short", "thorough"):
        prefer = "short"
    sg = obj.get("subgoals")
    if not isinstance(sg, list):
        sg = []
    subgoals = [str(x).strip() for x in sg[:5] if str(x).strip()]
    notes = str(obj.get("notes") or "").strip()[:240]
    raw_sc = obj.get("scenarios")
    scenarios: List[Dict[str, str]] = []
    if isinstance(raw_sc, list):
        for it in raw_sc[:4]:
            if not isinstance(it, dict):
                continue
            br = str(it.get("branch") or it.get("if") or "").strip()[:200]
            im = str(it.get("implication") or it.get("then") or "").strip()[:240]
            if br or im:
                scenarios.append({"branch": br, "implication": im})
    out: Dict[str, Any] = {
        "depth": depth,
        "subgoals": subgoals,
        "prefer": prefer,
        "notes": notes,
        "source": "llm_outline",
    }
    if scenarios:
        out["scenarios"] = scenarios
    return out
