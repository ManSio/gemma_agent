"""
Goal Runner 2.0 (MVP → Autonomy 3.0).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.brain.goal_runner_nudge import warrants_multistep_goal_text
from core.event_bus import bus
from core.goal_plan_validate import validate_with_optional_fix
from core.goal_runner_learner import get_goal_runner_learner
from core.goal_runner_types import classify_goal_runner_need, TaskType
from core.models import Output
from core.tool_args_normalize import normalize_brain_tool_args
from core.tools import list_tools, run_tool

logger = logging.getLogger(__name__)

STATE_KEY = "goal_runner_v2"
GOAL_RUNNER_VERSION = "3.0.0"

_PLANNING_MODEL = os.getenv("GOAL_RUNNER_PLANNING_MODEL")
_MONITOR_MODEL = os.getenv("GOAL_RUNNER_MONITOR_MODEL")


def _goal_runner_llm_provider(orchestrator: Any) -> Any:
    """
    Планировщик Goal Runner вызывает LLM. У Orchestrator нет поля openrouter — используем
    get_openrouter_provider(); если у объекта явно задан атрибут (в т.ч. None в тестах), он важнее.
    """
    if orchestrator is not None and hasattr(orchestrator, "openrouter"):
        return orchestrator.openrouter
    from core.openrouter_provider import get_openrouter_provider

    return get_openrouter_provider()


def executor_mode() -> bool:
    """Один переключатель «исполнитель задач»: см. GOAL_RUNNER_EXECUTOR_MODE / GOAL_RUNNER_ULTIMATE."""
    raw = os.getenv("GOAL_RUNNER_EXECUTOR_MODE") or os.getenv("GOAL_RUNNER_ULTIMATE")
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    if executor_mode():
        return True
    return os.getenv("GOAL_RUNNER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def steal_turns() -> bool:
    return os.getenv("GOAL_RUNNER_STEAL_TURNS", "").strip().lower() in {"1", "true", "yes", "on"}


def run_all_in_one() -> bool:
    if executor_mode():
        return True
    return os.getenv("GOAL_RUNNER_RUN_ALL_IN_ONE", "").strip().lower() in {"1", "true", "yes", "on"}


def autonomous_agent() -> bool:
    if executor_mode():
        return True
    return os.getenv("GOAL_RUNNER_AUTONOMOUS_AGENT", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_autostart(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def auto_start_from_nl() -> bool:
    """Запуск цели с естественного языка без /goal_run. Выкл: GOAL_RUNNER_AUTO_START=false."""
    if not enabled():
        return False
    # EXECUTOR_MODE включает инфраструктуру runner, но не перебивает явный AUTO_START=false (prod).
    return _env_autostart("GOAL_RUNNER_AUTO_START", default=True)


def _auto_start_smart_llm_enabled() -> bool:
    """
    Если эвристика не сработала — короткий запрос к LLM: многошаговая цель или нет.
    По умолчанию вкл. (удобный продукт без /goal_run); выкл: GOAL_RUNNER_AUTO_START_SMART=false.
    """
    return _env_autostart("GOAL_RUNNER_AUTO_START_SMART", default=True)


def _auto_start_smart_min_chars() -> int:
    """Не вызывать LLM на короткие реплики (экономия API)."""
    try:
        return max(48, min(600, int((os.getenv("GOAL_RUNNER_AUTO_START_SMART_MIN_CHARS") or "120").strip() or "120")))
    except ValueError:
        return 120


def _blocks_auto_new_goal(st: Optional[Dict[str, Any]]) -> bool:
    if not st:
        return False
    return str(st.get("status") or "") in {"running", "blocked", "awaiting_clarify"}


def _weak_tool_result(tool_name: str, result: Any) -> bool:
    """
    Успешный ответ без error, но по сути пустой (типичный затык поиска).
    Используется в executor_mode для инициации replan.
    """
    if not isinstance(result, dict) or result.get("error"):
        return False
    if result.get("ok") is False:
        return False
    if tool_name == "UniversalSearch.search":
        if str(result.get("summary") or "").strip():
            return False
        rs = result.get("results")
        return isinstance(rs, list) and len(rs) == 0
    if tool_name == "LawSearch.search":
        rs = result.get("results")
        return isinstance(rs, list) and len(rs) == 0
    if tool_name == "DocumentCorpus.unified_search":
        hs = result.get("hits")
        return isinstance(hs, list) and len(hs) == 0
    if tool_name == "LawSearch.keyword_search":
        hs = result.get("hits")
        return isinstance(hs, list) and len(hs) == 0
    if tool_name == "UserKnowledgeArchive.archive_search":
        items = result.get("items")
        return isinstance(items, list) and len(items) == 0
    if tool_name == "Wikipedia.scan":
        if str(result.get("text") or "").strip():
            return False
        return bool(result.get("ok") is not False and not result.get("error"))
    return False


def _max_plan_steps() -> int:
    try:
        return max(2, min(24, int((os.getenv("GOAL_RUNNER_MAX_PLAN_STEPS") or "10").strip() or "10")))
    except ValueError:
        return 10


def _max_tools_per_message() -> int:
    try:
        return max(1, min(8, int((os.getenv("GOAL_RUNNER_MAX_TOOLS_PER_MESSAGE") or "3").strip() or "3")))
    except ValueError:
        return 3


def _max_step_retries() -> int:
    try:
        return max(0, min(5, int((os.getenv("GOAL_RUNNER_STEP_RETRIES") or "2").strip() or "2")))
    except ValueError:
        return 2


def _max_replans() -> int:
    try:
        return max(0, min(6, int((os.getenv("GOAL_RUNNER_MAX_REPLANS") or "2").strip() or "2")))
    except ValueError:
        return 2


def _max_wall_sec() -> int:
    try:
        return max(0, min(3600, int((os.getenv("GOAL_RUNNER_MAX_WALL_TIME_SEC") or "300").strip() or "300")))
    except ValueError:
        return 300


def _run_all_max_tools() -> int:
    try:
        return max(5, min(80, int((os.getenv("GOAL_RUNNER_RUN_ALL_MAX_TOOLS") or "48").strip() or "48")))
    except ValueError:
        return 48


def _run_all_max_total_tools() -> int:
    try:
        return max(20, min(500, int((os.getenv("GOAL_RUNNER_RUN_ALL_MAX_TOTAL_TOOLS") or "160").strip() or "160")))
    except ValueError:
        return 160


def _max_autonomous_controller_calls() -> int:
    try:
        return max(0, min(80, int((os.getenv("GOAL_RUNNER_AUTONOMOUS_MAX_CONTROLLER") or "24").strip() or "24")))
    except ValueError:
        return 24


def _max_tail_replans() -> int:
    try:
        return max(0, min(12, int((os.getenv("GOAL_RUNNER_AUTONOMOUS_MAX_TAIL_REPLANS") or "5").strip() or "5")))
    except ValueError:
        return 5


def _goal_runner_telegram_progress_enabled() -> bool:
    """Пульс статуса в Telegram на этапах Goal Runner. По умолчанию вкл.; выкл: GOAL_RUNNER_TELEGRAM_PROGRESS=false."""
    raw = os.getenv("GOAL_RUNNER_TELEGRAM_PROGRESS")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _goal_runner_step_progress_text(plan: List[Any], idx: int, step: Dict[str, Any]) -> str:
    n = max(len(plan), 1)
    kind = str(step.get("kind") or "tool")
    if kind == "answer":
        return f"🎯 Goal Runner: шаг {idx + 1}/{n} — финальный ответ"
    tool_name = str(step.get("tool") or "").strip()
    note = str(step.get("note") or "").strip()
    if len(note) > 80:
        note = note[:77] + "…"
    if tool_name and note:
        return f"🎯 Goal Runner: шаг {idx + 1}/{n} — {tool_name} ({note})"
    if tool_name:
        return f"🎯 Goal Runner: шаг {idx + 1}/{n} — {tool_name}"
    return f"🎯 Goal Runner: шаг {idx + 1}/{n}"


async def _goal_runner_progress(text: str, *, force: bool = True) -> None:
    if not _goal_runner_telegram_progress_enabled():
        return
    try:
        from core.brain.vision_llm import brain_progress

        await brain_progress(text, force=force)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'goal_runner', e, exc_info=True)
def _artifacts_from_plan(plan: List[Any], upto_idx: int) -> List[str]:
    arts: List[str] = []
    for i, p in enumerate(plan[:upto_idx]):
        if not isinstance(p, dict):
            continue
        if str(p.get("kind") or "") == "tool" and str(p.get("status") or "") == "ok":
            ex = str(p.get("result_excerpt") or "")
            tn = str(p.get("tool") or "")
            if ex:
                arts.append(f"Шаг {i+1} ({tn}): {ex[:2500]}")
    return arts


def _allowed_tool_names() -> frozenset:
    try:
        return frozenset(list_tools().keys())
    except Exception:
        return frozenset()


def _goal_memory_append(st: Dict[str, Any], kind: str, detail: Any, max_n: int = 48) -> None:
    gm = st.setdefault("goal_memory", [])
    gm.append({"t": _now(), "kind": kind, "detail": detail})
    if len(gm) > max_n:
        st["goal_memory"] = gm[-max_n:]


def _sanitize_plan_tools(plan: List[Dict[str, Any]], allowed: frozenset) -> None:
    for row in plan:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "").lower() != "tool":
            continue
        tn = str(row.get("tool") or "").strip()
        if tn and allowed and tn not in allowed:
            bad = tn
            row["tool"] = ""
            row["note"] = f"{str(row.get('note') or '')[:400]} [инструмент «{bad}» не в каталоге]".strip()


def _wall_exceeded(st: Dict[str, Any]) -> tuple[bool, str]:
    lim = _max_wall_sec()
    if lim <= 0:
        return False, ""
    t0 = float(st.get("started_at_unix") or 0.0)
    if t0 <= 0:
        return False, ""
    elapsed = time.time() - t0
    if elapsed > lim:
        return True, f"лимит времени {lim}s (прошло {int(elapsed)}s)"
    return False, ""


def _tools_catalog() -> str:
    try:
        names = sorted(list_tools().keys())
    except Exception:
        names = []
    cap = max(20, min(200, int((os.getenv("GOAL_RUNNER_TOOLS_CATALOG_CAP") or "120").strip() or "120")))
    return ", ".join(names[:cap])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(orchestrator: Any, user_id: str, group_id: Optional[str]) -> Optional[Dict[str, Any]]:
    rec = orchestrator.behavior_store.load(user_id, group_id)
    st = rec.get(STATE_KEY)
    return st if isinstance(st, dict) else None


def _save(orchestrator: Any, user_id: str, group_id: Optional[str], state: Optional[Dict[str, Any]]) -> None:
    rec = orchestrator.behavior_store.load(user_id, group_id)
    if state is None:
        rec.pop(STATE_KEY, None)
    else:
        rec[STATE_KEY] = state
    orchestrator.behavior_store.save(user_id, group_id, rec)


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    t = (raw or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _plan_rows_from_llm_steps(steps_in: List[Any], *, start_id: int = 0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cap = _max_plan_steps()
    for j, row in enumerate(steps_in[:cap]):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "tool").strip().lower()
        if kind not in {"tool", "answer"}:
            kind = "tool"
        out.append(
            {
                "id": start_id + j,
                "kind": kind,
                "tool": str(row.get("tool") or "").strip(),
                "args": row.get("args") if isinstance(row.get("args"), dict) else {},
                "note": str(row.get("note") or "")[:500],
                "status": "pending",
                "result_excerpt": "",
                "retries": 0,
            }
        )
    return out


def _auto_run_enabled() -> bool:
    return run_all_in_one() or autonomous_agent()


async def _llm_plan(orchestrator: Any, user_goal: str) -> Tuple[Optional[Dict[str, Any]], str]:
    catalog = _tools_catalog()
    sys = (
        "Ты планировщик. Верни только один JSON-объект без markdown. Поля:\n"
        '- goal_summary: кратко на русском\n'
        '- steps: массив шагов, каждый: {"kind":"tool"|"answer","tool":"Имя.метод или пусто",'
        '"args":{},"note":"зачем шаг"}\n'
        "- clarify: null или строка вопроса пользователю (если без шагов)\n"
        "kind=tool только для имён из каталога. kind=answer — финальный ответ без инструмента (резюме).\n"
        "Последний шаг обычно kind=answer.\n"
        f"Каталог инструментов: {catalog}"
    )
    user = f"Цель пользователя:\n{user_goal[:8000]}"
    try:
        prov = _goal_runner_llm_provider(orchestrator)
        if prov is None:
            return None, "нет LLM провайдера"
        res = await prov.generate(
            user,
            system_prompt=sys,
            max_tokens=1800,
            temperature=0.2,
            telemetry_tag="goal_runner_plan",
        )
        if isinstance(res, dict) and res.get("error"):
            return None, str(res.get("error") or "llm error")
        text = str((res or {}).get("content") or "").strip() if isinstance(res, dict) else ""
        data = _parse_json_object(text)
        if not isinstance(data, dict):
            return None, "план не распознан"
        return data, ""
    except Exception as e:
        logger.warning("goal_runner plan: %s", e)
        return None, str(e)[:400]


async def _llm_classify_multistep_goal(orchestrator: Any, user_text: str) -> bool:
    """True, если по смыслу нужен Goal Runner (пошаговый план), иначе False."""
    try:
        prov = _goal_runner_llm_provider(orchestrator)
        if prov is None:
            return False
        sys = (
            "Ты классификатор. Пользователь пишет боту задачу.\n"
            "Ответь ровно одной латинской буквой:\n"
            "Y — нужен пошаговый план: несколько действий подряд, разные источники или инструменты, "
            "сравнение вариантов, длинная инструкция, цепочка «сначала—потом», исследование темы.\n"
            "N — достаточно одного ответа без плана (короткий вопрос, приветствие, один простой запрос).\n"
            "Ничего кроме Y или N не пиши."
        )
        user = (user_text or "").strip()[:4000]
        res = await prov.generate(
            user,
            system_prompt=sys,
            max_tokens=8,
            temperature=0.0,
            telemetry_tag="goal_runner_auto_smart",
        )
        if isinstance(res, dict) and res.get("error"):
            return False
        text = str((res or {}).get("content") or "").strip().upper() if isinstance(res, dict) else ""
        if not text:
            return False
        return text[0] == "Y"
    except Exception as e:
        logger.debug("goal_runner auto_smart: %s", e)
        return False


async def _llm_replan(
    orchestrator: Any,
    st: Dict[str, Any],
    fail_idx: int,
    tool_name: str,
    err: str,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Новый хвост плана с fail_idx или give_up."""
    catalog = _tools_catalog()
    done_summary: List[Dict[str, Any]] = []
    for i, s in enumerate(st.get("plan") or []):
        if i < fail_idx and isinstance(s, dict):
            done_summary.append(
                {
                    "step": i,
                    "kind": s.get("kind"),
                    "tool": s.get("tool"),
                    "status": s.get("status"),
                    "excerpt": (str(s.get("result_excerpt") or ""))[:400],
                }
            )
    sys = (
        "План агента сорвался на шаге инструмента. Верни только один JSON без markdown.\n"
        'Либо {"give_up":"кратко на русском почему нельзя продолжить"}\n'
        'Либо {"steps":[{"kind":"tool"|"answer","tool":"Имя.метод","args":{},"note":""},...]}\n'
        "steps — только замена текущего и следующие шаги (хвост плана). Последний обычно kind=answer.\n"
        f"Каталог инструментов: {catalog}"
    )
    user = json.dumps(
        {
            "goal": str(st.get("user_goal") or "")[:4000],
            "failed_step_index": fail_idx,
            "failed_tool": tool_name,
            "error": err[:800],
            "completed": done_summary,
        },
        ensure_ascii=False,
    )
    try:
        prov = _goal_runner_llm_provider(orchestrator)
        if prov is None:
            return None, "нет LLM"
        res = await prov.generate(
            user,
            system_prompt=sys,
            max_tokens=1600,
            temperature=0.25,
            telemetry_tag="goal_runner_replan",
        )
        if isinstance(res, dict) and res.get("error"):
            return None, str(res.get("error"))
        text = str((res or {}).get("content") or "").strip() if isinstance(res, dict) else ""
        data = _parse_json_object(text)
        if not isinstance(data, dict):
            return None, "replan не распознан"
        give = data.get("give_up")
        if isinstance(give, str) and give.strip():
            return None, give.strip()
        steps_in = data.get("steps")
        if not isinstance(steps_in, list) or not steps_in:
            return None, "replan без шагов"
        out = _plan_rows_from_llm_steps(steps_in, start_id=fail_idx)
        if not out:
            return None, "пустой replan"
        allowed = _allowed_tool_names()
        _sanitize_plan_tools(out, allowed)
        verr = validate_with_optional_fix(out, allowed)
        if verr:
            _goal_memory_append(st, "replan_rejected", verr[:10])
            return None, "план: " + "; ".join(verr[:6])
        return out, None
    except Exception as e:
        logger.warning("goal_runner replan: %s", e)
        return None, str(e)[:400]


async def _llm_synthesize(orchestrator: Any, user_goal: str, artifacts: List[str]) -> str:
    sys = "Собери итог для пользователя по русски: чётко, структурно, без JSON и без внутренних рассуждений."
    body = f"Цель:\n{user_goal}\n\nРезультаты шагов:\n" + "\n---\n".join(artifacts[-12:])
    try:
        prov = _goal_runner_llm_provider(orchestrator)
        if prov is None:
            return "Итог: провайдер LLM недоступен."
        res = await prov.generate(
            body[:12000],
            system_prompt=sys,
            max_tokens=2500,
            temperature=0.35,
            telemetry_tag="goal_runner_synth",
        )
        if isinstance(res, dict) and res.get("error"):
            return f"Итог недоступен: {res.get('error')}"
        return str((res or {}).get("content") or "").strip() if isinstance(res, dict) else ""
    except Exception as e:
        return f"Не удалось собрать итог: {e}"


async def _llm_autonomous_decide(
    orchestrator: Any,
    st: Dict[str, Any],
    idx: int,
    plan: List[Any],
) -> Dict[str, Any]:
    catalog = _tools_catalog()
    prev: List[Dict[str, Any]] = []
    for i in range(max(0, idx - 3), idx):
        p = plan[i] if i < len(plan) else None
        if isinstance(p, dict):
            prev.append(
                {
                    "i": i,
                    "kind": p.get("kind"),
                    "status": p.get("status"),
                    "excerpt": (str(p.get("result_excerpt") or ""))[:500],
                }
            )
    nxt: List[Dict[str, Any]] = []
    for i in range(idx, min(len(plan), idx + 5)):
        p = plan[i]
        if isinstance(p, dict):
            nxt.append(
                {
                    "i": i,
                    "kind": p.get("kind"),
                    "tool": p.get("tool"),
                    "note": (str(p.get("note") or ""))[:120],
                }
            )
    sys = (
        "Ты метаконтроллер автономного агента: реши следующий ход по фактам плана.\n"
        "Верни один JSON без markdown:\n"
        '{"action":"continue"|"ask_user"|"replan"|"finish_early",'
        '"question":null или одна строка,'
        '"steps":null или массив шагов как у планировщика (для replan — замена с текущего шага),'
        '"note":"кратко по-русски (для finish_early — зачем завершить)"}\n'
        "continue — выполнять план дальше.\n"
        "ask_user — без человека нельзя; один конкретный вопрос в question.\n"
        "replan — хвост плана неверен; steps заменяют текущий и следующие шаги.\n"
        "finish_early — цель достигнута по уже собранным данным; не выдумывай факты.\n"
        f"Каталог инструментов: {catalog}"
    )
    user = json.dumps(
        {
            "goal": str(st.get("user_goal") or "")[:4000],
            "summary": str(st.get("goal_summary") or "")[:1500],
            "current_step": idx,
            "done_recent": prev,
            "planned_next": nxt,
        },
        ensure_ascii=False,
    )
    try:
        prov = _goal_runner_llm_provider(orchestrator)
        if prov is None:
            return {"action": "continue"}
        res = await prov.generate(
            user,
            system_prompt=sys,
            max_tokens=1400,
            temperature=0.25,
            telemetry_tag="goal_runner_autonomous_decide",
        )
        if isinstance(res, dict) and res.get("error"):
            return {"action": "continue"}
        text = str((res or {}).get("content") or "").strip() if isinstance(res, dict) else ""
        data = _parse_json_object(text)
        if not isinstance(data, dict):
            return {"action": "continue"}
        return data
    except Exception as e:
        logger.warning("goal_runner autonomous_decide: %s", e)
        return {"action": "continue"}


def _format_status(st: Dict[str, Any]) -> str:
    lines = [
        "🎯 Goal Runner",
        f"Статус: {st.get('status')}",
        f"Цель: {st.get('user_goal', '')[:300]}",
        f"Шаг: {int(st.get('current_step', 0)) + 1}/{len(st.get('plan') or [])}",
    ]
    if str(st.get("status") or "") == "awaiting_clarify":
        cq = str(st.get("clarify_prompt") or "").strip()
        if cq:
            lines.append(f"❓ Ожидается ответ: {cq[:500]}")
    for i, s in enumerate(st.get("plan") or []):
        if not isinstance(s, dict):
            continue
        lines.append(f"  {i+1}. {s.get('kind')} {s.get('tool', '')} — {s.get('status')} {s.get('note', '')[:80]}")
    return "\n".join(lines)


async def _execute_goal_steps(
    orchestrator: Any,
    user_id: str,
    group_id: Optional[str],
    *,
    per_batch_tool_limit: int,
    run_all: bool,
    accumulated: Optional[List[str]] = None,
    total_tools_this_run: int = 0,
) -> Optional[List[Output]]:
    allowed = _allowed_tool_names()
    acc = list(accumulated or [])
    total_tools = int(total_tools_this_run)
    max_total = _run_all_max_total_tools()

    st = _load(orchestrator, user_id, group_id)
    if not st:
        return [Output(type="text", payload="Goal Runner: нет активной задачи.", meta={"goal_runner": True})]

    w_bad, w_msg = _wall_exceeded(st)
    if w_bad:
        st["status"] = "timeout"
        st["updated_at"] = _now()
        _save(orchestrator, user_id, group_id, st)
        orchestrator.behavior_store.patch_session_task(
            user_id,
            group_id,
            {"goal_runner": "timeout"},
        )
        parts = acc + ([f"⏱ Goal Runner: {w_msg}"] if w_msg else [])
        get_goal_runner_learner().record_outcome(
            str(st.get("user_goal") or ""), "timeout",
            duration_s=float(st.get("started_at_unix", 0)) and time.time() - float(st.get("started_at_unix", 0)),
        )
        return [Output(type="text", payload="\n".join([p for p in parts if p]), meta={"goal_runner": True})]

    status = str(st.get("status") or "")
    if status not in {"running", "blocked"}:
        return None
    if status == "blocked":
        st["status"] = "running"

    plan = st.get("plan")
    if not isinstance(plan, list) or not plan:
        _save(orchestrator, user_id, group_id, None)
        get_goal_runner_learner().record_outcome(
            str(st.get("user_goal") or ""), "done_fail",
            duration_s=float(st.get("started_at_unix", 0)) and time.time() - float(st.get("started_at_unix", 0)),
            error="plan_lost",
        )
        return [Output(type="text", payload="Goal Runner: план потерян, сброс.", meta={"goal_runner": True})]

    idx = int(st.get("current_step", 0))
    if idx >= len(plan):
        _save(orchestrator, user_id, group_id, None)
        get_goal_runner_learner().record_outcome(
            str(st.get("user_goal") or ""), "done_ok",
            duration_s=float(st.get("started_at_unix", 0)) and time.time() - float(st.get("started_at_unix", 0)),
        )
        return [Output(type="text", payload="Goal Runner: все шаги уже выполнены.", meta={"goal_runner": True})]

    tools_left = max(1, int(per_batch_tool_limit))
    msg_parts: List[str] = []
    replan_restart = False

    while idx < len(plan) and tools_left > 0:
        step = plan[idx]
        if not isinstance(step, dict):
            idx += 1
            continue
        kind = step.get("kind", "tool")
        if kind == "answer":
            await _goal_runner_progress(_goal_runner_step_progress_text(plan, idx, step), force=True)
            arts = _artifacts_from_plan(plan, idx)
            final = await _llm_synthesize(orchestrator, str(st.get("user_goal") or ""), arts)
            st["status"] = "done"
            st["current_step"] = idx + 1
            st["updated_at"] = _now()
            st.setdefault("step_log", []).append({"step": idx, "kind": "answer", "at": _now()})
            _save(orchestrator, user_id, group_id, st)
            orchestrator.behavior_store.patch_session_task(
                user_id,
                group_id,
                {"goal_runner": "done"},
            )
            payload = "\n".join(acc + ["", final]) if (run_all and acc) else final
            get_goal_runner_learner().record_outcome(
                str(st.get("user_goal") or ""), "done_ok",
                duration_s=float(st.get("started_at_unix", 0)) and time.time() - float(st.get("started_at_unix", 0)),
                had_tools=bool(plan and any(isinstance(s, dict) and s.get("kind") == "tool" and s.get("status") == "ok" for s in plan)),
                tool_count=sum(1 for s in (plan or []) if isinstance(s, dict) and s.get("kind") == "tool" and s.get("status") == "ok"),
            )
            return [Output(type="text", payload=payload, meta={"goal_runner": True, "goal_final": True})]

        tool_name = str(step.get("tool") or "").strip()
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        if not tool_name:
            step["status"] = "error"
            step["result_excerpt"] = "нет имени инструмента"
            st["status"] = "blocked"
            st["updated_at"] = _now()
            _save(orchestrator, user_id, group_id, st)
            return [
                Output(
                    type="text",
                    payload=f"Шаг {idx+1}: не указан tool. /goal_status или /goal_cancel.",
                    meta={"goal_runner": True},
                )
            ]

        await _goal_runner_progress(_goal_runner_step_progress_text(plan, idx, step), force=True)

        if allowed and tool_name not in allowed:
            result: Any = {"error": f"инструмент не в каталоге: {tool_name}"}
        else:
            merged = dict(args)
            merged["user_id"] = str(user_id)
            merged = normalize_brain_tool_args(tool_name, merged)
            try:
                result = await run_tool(tool_name, **merged)
            except Exception as e:
                result = {"error": str(e)}
        try:
            err = ""
            ok = True
            if isinstance(result, dict) and result.get("error"):
                ok = False
                err = str(result.get("error") or "")
            bus.emit(
                "brain.tool_finished",
                {
                    "user_id": str(user_id),
                    "group_id": str(group_id).strip() if group_id not in (None, "") else None,
                    "tool_name": tool_name,
                    "tool_ok": ok,
                    "tool_error": err[:800],
                },
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'goal_runner', e, exc_info=True)
        excerpt = ""
        if isinstance(result, dict):
            if result.get("error"):
                excerpt = f"error: {result.get('error')}"
            else:
                excerpt = json.dumps(result, ensure_ascii=False)[:3500]
        else:
            excerpt = str(result)[:3500]

        step["result_excerpt"] = excerpt
        st.setdefault("step_log", []).append(
            {"step": idx, "tool": tool_name, "at": _now(), "ok": not (isinstance(result, dict) and result.get("error"))}
        )

        if isinstance(result, dict) and result.get("error"):
            step["retries"] = int(step.get("retries") or 0) + 1
            if step["retries"] > _max_step_retries():
                rc = int(st.get("replan_count") or 0)
                if rc < _max_replans():
                    new_tail, rinfo = await _llm_replan(
                        orchestrator, st, idx, tool_name, str(result.get("error") or "")
                    )
                    if new_tail:
                        head = plan[:idx]
                        st["plan"] = head + new_tail
                        plan = st["plan"]
                        st["replan_count"] = rc + 1
                        for j in range(idx, len(plan)):
                            row = plan[j]
                            if isinstance(row, dict):
                                row["status"] = "pending"
                                row["retries"] = 0
                                row["result_excerpt"] = ""
                        st["current_step"] = idx
                        st["status"] = "running"
                        st["updated_at"] = _now()
                        _goal_memory_append(
                            st,
                            "error_replan_applied",
                            {"step": idx, "tool": tool_name, "tail": len(new_tail)},
                        )
                        _save(orchestrator, user_id, group_id, st)
                        await _goal_runner_progress(
                            "🎯 Goal Runner: перестраиваю план после ошибки шага…",
                            force=True,
                        )
                        if run_all:
                            acc.append(f"↻ План перестроен после шага {idx+1} (replan {st['replan_count']}/{_max_replans()}).")
                        replan_restart = True
                        break
                    step["status"] = "error"
                    st["status"] = "blocked"
                    st["updated_at"] = _now()
                    _save(orchestrator, user_id, group_id, st)
                    detail = (rinfo or "replan не удался")[:500]
                    body = "\n".join(msg_parts + [f"Шаг {idx+1} ({tool_name}): лимит повторов. {detail}. /goal_cancel."])
                    if run_all and acc:
                        body = "\n".join(acc + ["", body])
                    return [Output(type="text", payload=body, meta={"goal_runner": True})]
                step["status"] = "error"
                st["status"] = "blocked"
                st["updated_at"] = _now()
                _save(orchestrator, user_id, group_id, st)
                tail = f"Шаг {idx+1} ({tool_name}): {result.get('error')}. Лимит повторов. /goal_step после правки или /goal_cancel."
                body = "\n".join(msg_parts + [tail]) if msg_parts else tail
                if run_all and acc:
                    body = "\n".join(acc + ["", body])
                return [Output(type="text", payload=body, meta={"goal_runner": True})]
            st["updated_at"] = _now()
            _save(orchestrator, user_id, group_id, st)
            return [
                Output(
                    type="text",
                    payload=(
                        f"Шаг {idx+1} ({tool_name}): ошибка {result.get('error')}. "
                        f"Повтор {step['retries']}/{_max_step_retries()}. /goal_step"
                    ),
                    meta={"goal_runner": True},
                )
            ]

        if (
            executor_mode()
            and isinstance(result, dict)
            and not result.get("error")
            and _weak_tool_result(tool_name, result)
        ):
            rcw = int(st.get("replan_count") or 0)
            if rcw < _max_replans():
                new_tail_w, _rinfow = await _llm_replan(
                    orchestrator,
                    st,
                    idx,
                    tool_name,
                    "Пустая или недостаточная выдача инструмента; нужен другой запрос, источник или инструмент.",
                )
                if new_tail_w:
                    head_w = plan[:idx]
                    st["plan"] = head_w + new_tail_w
                    plan = st["plan"]
                    st["replan_count"] = rcw + 1
                    for j in range(idx, len(plan)):
                        row = plan[j]
                        if isinstance(row, dict):
                            row["status"] = "pending"
                            row["retries"] = 0
                            row["result_excerpt"] = ""
                    st["current_step"] = idx
                    st["status"] = "running"
                    st["updated_at"] = _now()
                    _goal_memory_append(
                        st,
                        "weak_replan_applied",
                        {"step": idx, "tool": tool_name, "tail": len(new_tail_w)},
                    )
                    _save(orchestrator, user_id, group_id, st)
                    await _goal_runner_progress(
                        "🎯 Goal Runner: слабая выдача — перестраиваю план…",
                        force=True,
                    )
                    if run_all:
                        acc.append(
                            f"↻ План перестроен (пустая выдача), шаг {idx+1} "
                            f"(replan {st['replan_count']}/{_max_replans()})."
                        )
                    replan_restart = True
                    break

        step["status"] = "ok"
        msg_parts.append(f"✓ Шаг {idx+1}: {tool_name}")
        idx += 1
        st["current_step"] = idx
        tools_left -= 1
        total_tools += 1
        if run_all and total_tools >= max_total:
            st["status"] = "blocked"
            st["updated_at"] = _now()
            _save(orchestrator, user_id, group_id, st)
            cap_msg = f"Лимит инструментов за один запуск ({max_total}). /goal_step или /goal_cancel."
            body = "\n".join(acc + msg_parts + ["", cap_msg]) if (acc or msg_parts) else cap_msg
            return [Output(type="text", payload=body, meta={"goal_runner": True})]

    if replan_restart:
        return await _execute_goal_steps(
            orchestrator,
            user_id,
            group_id,
            per_batch_tool_limit=per_batch_tool_limit,
            run_all=run_all,
            accumulated=acc if run_all else None,
            total_tools_this_run=total_tools,
        )

    if (
        run_all
        and autonomous_agent()
        and _max_autonomous_controller_calls() > 0
        and idx < len(plan)
    ):
        cc = int(st.get("controller_calls") or 0)
        if cc < _max_autonomous_controller_calls():
            decision = await _llm_autonomous_decide(orchestrator, st, idx, plan)
            st["controller_calls"] = cc + 1
            act = str(decision.get("action") or "continue").lower().strip()
            if act == "ask_user":
                q = str(decision.get("question") or "").strip()
                if q:
                    st["status"] = "awaiting_clarify"
                    st["clarify_prompt"] = q[:2000]
                    st["clarify_from_controller"] = True
                    st["updated_at"] = _now()
                    st["current_step"] = idx
                    _save(orchestrator, user_id, group_id, st)
                    orchestrator.behavior_store.patch_session_task(
                        user_id,
                        group_id,
                        {"goal_runner": "awaiting_clarify"},
                    )
                    prelude = "\n".join(acc + msg_parts) if (acc or msg_parts) else ""
                    body = (prelude + "\n\n" if prelude else "") + (
                        f"🤔 {q}\n\nОтветьте обычным сообщением в чат (не командой) или /goal_cancel."
                    )
                    return [
                        Output(
                            type="text",
                            payload=body,
                            meta={"goal_runner": True, "goal_awaiting_clarify": True},
                        )
                    ]
            elif act == "replan":
                steps_in = decision.get("steps")
                if isinstance(steps_in, list) and steps_in:
                    trc = int(st.get("tail_replan_count") or 0)
                    if trc < _max_tail_replans():
                        new_rows = _plan_rows_from_llm_steps(steps_in, start_id=idx)
                        if new_rows:
                            _sanitize_plan_tools(new_rows, allowed)
                            verr = validate_with_optional_fix(new_rows, allowed)
                            if verr:
                                _goal_memory_append(st, "tail_replan_rejected", verr[:8])
                            else:
                                st["plan"] = plan[:idx] + new_rows
                                plan = st["plan"]
                                st["tail_replan_count"] = trc + 1
                                for j in range(idx, len(plan)):
                                    row = plan[j]
                                    if isinstance(row, dict):
                                        row["status"] = "pending"
                                        row["retries"] = 0
                                        row["result_excerpt"] = ""
                                st["current_step"] = idx
                                st["status"] = "running"
                                st["updated_at"] = _now()
                                _goal_memory_append(
                                    st,
                                    "tail_replan_applied",
                                    {"from_step": idx, "n": len(new_rows)},
                                )
                                _save(orchestrator, user_id, group_id, st)
                                await _goal_runner_progress(
                                    "🎯 Goal Runner: перестраиваю хвост плана…",
                                    force=True,
                                )
                                acc.append(
                                    f"◆ Хвост плана обновлён ({st['tail_replan_count']}/{_max_tail_replans()})."
                                )
                                return await _execute_goal_steps(
                                    orchestrator,
                                    user_id,
                                    group_id,
                                    per_batch_tool_limit=per_batch_tool_limit,
                                    run_all=True,
                                    accumulated=acc,
                                    total_tools_this_run=total_tools,
                                )
            elif act == "finish_early":
                arts = _artifacts_from_plan(plan, idx)
                hint = str(decision.get("note") or decision.get("finish_summary_hint") or "").strip()
                ug = str(st.get("user_goal") or "")
                if hint:
                    ug = ug + "\n\n[Раннее завершение: " + hint[:600] + "]"
                syn = await _llm_synthesize(orchestrator, ug, arts)
                st["status"] = "done"
                st["current_step"] = idx
                st["updated_at"] = _now()
                st.setdefault("step_log", []).append({"step": idx, "kind": "finish_early", "at": _now()})
                _save(orchestrator, user_id, group_id, st)
                orchestrator.behavior_store.patch_session_task(
                    user_id,
                    group_id,
                    {"goal_runner": "done"},
                )
                payload = "\n".join(acc + msg_parts + ["", syn]) if (acc or msg_parts) else syn
                get_goal_runner_learner().record_outcome(
                    str(st.get("user_goal") or ""), "done_ok",
                    duration_s=float(st.get("started_at_unix", 0)) and time.time() - float(st.get("started_at_unix", 0)),
                    had_tools=True,
                    tool_count=sum(1 for s in (plan or []) if isinstance(s, dict) and s.get("kind") == "tool" and s.get("status") == "ok"),
                )
                return [
                    Output(
                        type="text",
                        payload=payload,
                        meta={"goal_runner": True, "goal_final": True, "goal_finish_early": True},
                    )
                ]

    st["current_step"] = idx
    st["updated_at"] = _now()
    _save(orchestrator, user_id, group_id, st)

    if idx >= len(plan):
        arts = _artifacts_from_plan(plan, idx)
        await _goal_runner_progress("🎯 Goal Runner: формирую итоговый ответ…", force=True)
        syn = await _llm_synthesize(orchestrator, str(st.get("user_goal") or ""), arts)
        st["status"] = "done"
        st["updated_at"] = _now()
        _save(orchestrator, user_id, group_id, st)
        orchestrator.behavior_store.patch_session_task(user_id, group_id, {"goal_runner": "done"})
        payload = "\n".join(acc + msg_parts + ["", syn]) if (run_all and (acc or msg_parts)) else syn
        get_goal_runner_learner().record_outcome(
            str(st.get("user_goal") or ""), "done_ok",
            duration_s=float(st.get("started_at_unix", 0)) and time.time() - float(st.get("started_at_unix", 0)),
            had_tools=bool(plan and any(isinstance(s, dict) and s.get("kind") == "tool" and s.get("status") == "ok" for s in plan)),
            tool_count=sum(1 for s in (plan or []) if isinstance(s, dict) and s.get("kind") == "tool" and s.get("status") == "ok"),
        )
        return [Output(type="text", payload=payload, meta={"goal_runner": True, "goal_final": True})]

    if not run_all:
        tail = "\n".join(msg_parts) if msg_parts else "Шаг выполнен."
        tail += f"\n\n{_format_status(st)}\n/goal_step — далее"
        return [Output(type="text", payload=tail, meta={"goal_runner": True})]

    acc.extend(msg_parts)
    return await _execute_goal_steps(
        orchestrator,
        user_id,
        group_id,
        per_batch_tool_limit=per_batch_tool_limit,
        run_all=True,
        accumulated=acc,
        total_tools_this_run=total_tools,
    )


async def _resume_after_clarify(
    orchestrator: Any,
    user_id: str,
    group_id: Optional[str],
    st: Dict[str, Any],
    answer: str,
) -> List[Output]:
    hist = list(st.get("clarification_history") or [])
    qp = str(st.get("clarify_prompt") or "").strip()
    if qp or st.get("clarify_from_controller"):
        hist.append({"q": qp or "(уточнение)", "a": (answer or "").strip()[:4000]})
    chunks = [str(st.get("user_goal") or "")]
    for h in hist[-8:]:
        chunks.append(f"Вопрос: {h.get('q')}\nОтвет: {h.get('a')}")
    merged = "\n\n".join(chunks)[:12000]
    await _goal_runner_progress("🎯 Goal Runner: обновляю план после уточнения…", force=True)
    plan_data, err = await _llm_plan(orchestrator, merged)
    if not plan_data:
        return [
            Output(
                type="text",
                payload=f"Не удалось обновить план: {err}",
                meta={"goal_runner": True},
            )
        ]
    clarify = plan_data.get("clarify")
    if isinstance(clarify, str) and clarify.strip():
        st["clarify_prompt"] = clarify.strip()[:2000]
        st["clarification_history"] = hist
        st["user_goal"] = merged[:8000]
        st["updated_at"] = _now()
        _save(orchestrator, user_id, group_id, st)
        orchestrator.behavior_store.patch_session_task(
            user_id,
            group_id,
            {"goal_runner": "awaiting_clarify"},
        )
        return [
            Output(
                type="text",
                payload=f"Нужно ещё уточнение:\n{clarify.strip()}",
                meta={"goal_runner": True, "goal_awaiting_clarify": True},
            )
        ]
    steps_in = plan_data.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        return [
            Output(
                type="text",
                payload="После уточнения план пуст. Переформулируйте цель или /goal_cancel.",
                meta={"goal_runner": True},
            )
        ]
    plan = _plan_rows_from_llm_steps(steps_in, start_id=0)
    if not plan:
        return [
            Output(
                type="text",
                payload="План не содержит шагов. /goal_cancel",
                meta={"goal_runner": True},
            )
        ]
    allowed = _allowed_tool_names()
    _sanitize_plan_tools(plan, allowed)
    verr = validate_with_optional_fix(plan, allowed)
    if verr:
        _goal_memory_append(st, "plan_rejected", {"phase": "after_clarify", "errors": verr[:12]})
        _save(orchestrator, user_id, group_id, st)
        return [
            Output(
                type="text",
                payload="План после уточнения не прошёл проверку:\n" + "\n".join(f"• {e}" for e in verr),
                meta={"goal_runner": True},
            )
        ]
    st["plan"] = plan
    st["current_step"] = 0
    st["status"] = "running"
    st["user_goal"] = merged[:8000]
    st["goal_summary"] = str(plan_data.get("goal_summary") or "")[:2000]
    st["clarification_history"] = hist
    st.pop("clarify_prompt", None)
    st.pop("clarify_from_controller", None)
    st["updated_at"] = _now()
    st["replan_count"] = 0
    st["controller_calls"] = 0
    st["tail_replan_count"] = 0
    _goal_memory_append(st, "plan_validated", {"phase": "after_clarify", "steps": len(plan)})
    _save(orchestrator, user_id, group_id, st)
    orchestrator.behavior_store.patch_session_task(
        user_id,
        group_id,
        {"goal_runner": "running", "goal_run_id": st.get("run_id", "")},
    )
    if _auto_run_enabled():
        return await _execute_goal_steps(
            orchestrator,
            user_id,
            group_id,
            per_batch_tool_limit=_run_all_max_tools(),
            run_all=True,
        )
    return [
        Output(
            type="text",
            payload=(
                f"План из {len(plan)} шагов (после уточнения).\n{_format_status(st)}\n\n/goal_step — далее"
            ),
            meta={"goal_runner": True},
        )
    ]


# ── Autonomous Goal Runner 2.0 (Autonomy 3.0) ──

_GOAL_DETECT_PATTERNS = (
    "хочу систему",
    "сделай проект",
    "нужно реализовать",
    "помоги построить",
    "создай проект",
    "реализуй",
    "построй систему",
    "спроектируй",
    "разработай",
    "хочу бота",
    "сделай бота",
    "хочу приложение",
    "сделай приложение",
    "создай приложение",
    "помоги с проектом",
    "собери проект",
    "построй проект",
)


def detect_goal(user_text: str) -> bool:
    """Detect if user text expresses a goal intent (Autonomy 3.0)."""
    low = (user_text or "").strip().lower()
    if not low:
        return False
    for pattern in _GOAL_DETECT_PATTERNS:
        if pattern in low:
            return True
    return False


def decompose_goal(goal_text: str) -> List[str]:
    """Decompose a goal into high-level steps (pure heuristic, no LLM)."""
    low = (goal_text or "").strip().lower()
    steps: List[str] = []

    if any(w in low for w in ("telegram", "бота", "бот", "чат")):
        steps.append("создать структуру проекта")
        steps.append("написать handlers")
        steps.append("добавить команды")
        steps.append("собрать docker-compose")
        steps.append("выдать инструкции по запуску")
        return steps

    if any(w in low for w in ("систем", "архитектур", "проект")):
        steps.append("определить требования")
        steps.append("спроектировать архитектуру")
        steps.append("реализовать ядро")
        steps.append("протестировать")
        steps.append("подготовить документацию")
        return steps

    steps.append("проанализировать запрос")
    steps.append("спланировать шаги")
    steps.append("выполнить шаги")
    steps.append("проверить результат")
    return steps


def monitor_progress(st: Dict[str, Any]) -> str:
    """Build a human-readable progress report for an active goal."""
    if not st:
        return "Goal Runner: нет активной задачи."
    plan = st.get("plan") or []
    current = int(st.get("current_step", 0))
    total = len(plan)
    status = st.get("status", "unknown")

    lines = [
        "📊 Goal Runner — мониторинг",
        f"Статус: {status}",
        f"Прогресс: {current}/{total} шагов",
    ]

    done = 0
    errors = 0
    pending = 0
    for s in plan:
        s_status = str(s.get("status") or "pending")
        if s_status == "ok":
            done += 1
        elif s_status == "error":
            errors += 1
        else:
            pending += 1

    lines.append(f"✓ выполнено: {done}  ✗ ошибок: {errors}  ○ ожидают: {pending}")

    replans = int(st.get("replan_count") or 0)
    if replans:
        lines.append(f"Перестроений плана: {replans}")

    gm = st.get("goal_memory") or []
    if gm:
        lines.append("")
        lines.append("Последние события:")
        for entry in gm[-5:]:
            if isinstance(entry, dict):
                lines.append(f"  • {entry.get('kind', '?')}: {str(entry.get('detail', ''))[:120]}")

    elapsed = ""
    t0 = float(st.get("started_at_unix") or 0)
    if t0 > 0:
        sec = int(time.time() - t0)
        if sec < 120:
            elapsed = f"{sec}s"
        else:
            elapsed = f"{sec // 60}m {sec % 60}s"
        lines.append(f"Времени с начала: {elapsed}")

    return "\n".join(lines)


async def _start_goal_run_from_rest(
    orchestrator: Any,
    user_id: str,
    group_id: Optional[str],
    rest: str,
) -> List[Output]:
    await _goal_runner_progress("🎯 Goal Runner: строю план…", force=True)
    plan_data, err = await _llm_plan(orchestrator, rest)
    if not plan_data:
        return [
            Output(
                type="text",
                payload=f"Не удалось построить план: {err}",
                meta={"goal_runner": True},
            )
        ]
    clarify = plan_data.get("clarify")
    if isinstance(clarify, str) and clarify.strip():
        if autonomous_agent():
            st_cl = {
                "version": 2,
                "run_id": uuid.uuid4().hex[:12],
                "status": "awaiting_clarify",
                "user_goal": rest[:8000],
                "goal_summary": str(plan_data.get("goal_summary") or "")[:2000],
                "plan": [],
                "current_step": 0,
                "step_log": [],
                "updated_at": _now(),
                "started_at_unix": time.time(),
                "replan_count": 0,
                "controller_calls": 0,
                "tail_replan_count": 0,
                "clarify_prompt": clarify.strip()[:2000],
                "clarification_history": [],
                "goal_memory": [],
            }
            _save(orchestrator, user_id, group_id, st_cl)
            orchestrator.behavior_store.patch_session_task(
                user_id,
                group_id,
                {"goal_runner": "awaiting_clarify", "goal_run_id": st_cl["run_id"]},
            )
            return [
                Output(
                    type="text",
                    payload=(
                        f"Нужно уточнение:\n{clarify.strip()}\n\n"
                        "Ответьте обычным сообщением в чат или /goal_cancel."
                    ),
                    meta={"goal_runner": True, "goal_awaiting_clarify": True},
                )
            ]
        return [Output(type="text", payload=f"Нужно уточнение:\n{clarify.strip()}", meta={"goal_runner": True})]
    steps_in = plan_data.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        return [Output(type="text", payload="План пуст. Переформулируй цель.", meta={"goal_runner": True})]
    plan = _plan_rows_from_llm_steps(steps_in, start_id=0)
    if not plan:
        return [Output(type="text", payload="План не содержит шагов.", meta={"goal_runner": True})]
    allowed = _allowed_tool_names()
    _sanitize_plan_tools(plan, allowed)
    verr = validate_with_optional_fix(plan, allowed)
    if verr:
        return [
            Output(
                type="text",
                payload="План не прошёл проверку:\n" + "\n".join(f"• {e}" for e in verr),
                meta={"goal_runner": True},
            )
        ]
    st = {
        "version": 2,
        "run_id": uuid.uuid4().hex[:12],
        "status": "running",
        "user_goal": rest[:8000],
        "goal_summary": str(plan_data.get("goal_summary") or "")[:2000],
        "plan": plan,
        "current_step": 0,
        "step_log": [],
        "updated_at": _now(),
        "started_at_unix": time.time(),
        "replan_count": 0,
        "controller_calls": 0,
        "tail_replan_count": 0,
        "goal_memory": [],
    }
    _goal_memory_append(st, "plan_validated", {"phase": "goal_run", "steps": len(plan)})
    _save(orchestrator, user_id, group_id, st)
    orchestrator.behavior_store.patch_session_task(
        user_id,
        group_id,
        {"goal_runner": "running", "goal_run_id": st["run_id"]},
    )
    if _auto_run_enabled():
        return await _execute_goal_steps(
            orchestrator,
            user_id,
            group_id,
            per_batch_tool_limit=_run_all_max_tools(),
            run_all=True,
        )
    return [
        Output(
            type="text",
            payload=(
                f"План из {len(plan)} шагов принят.\n{ _format_status(st) }\n\n"
                "Дальше: /goal_step — выполнить следующий шаг (до нескольких tool подряд)."
            ),
            meta={"goal_runner": True},
        )
    ]


async def try_goal_runner_turn(
    *,
    orchestrator: Any,
    user_id: str,
    group_id: Optional[str],
    user_text: str,
    source: str = "auto",
) -> Optional[List[Output]]:
    if not enabled() or not user_id:
        return None
    t = (user_text or "").strip()
    if not t:
        return None

    if t.startswith("/goal_cancel"):
        _save(orchestrator, user_id, group_id, None)
        orchestrator.behavior_store.patch_session_task(
            user_id,
            group_id,
            {"goal_runner": "cancelled"},
        )
        get_goal_runner_learner().record_outcome(t, "cancelled")
        return [Output(type="text", payload="Goal Runner: сброшен.", meta={"goal_runner": True})]

    if t.startswith("/goal_status"):
        st = _load(orchestrator, user_id, group_id)
        if not st:
            return [Output(type="text", payload="Goal Runner: нет активной задачи.", meta={"goal_runner": True})]
        body = _format_status(st)
        if str(st.get("status") or "") == "awaiting_clarify":
            body += "\n\nОтветьте обычным сообщением в чат или /goal_cancel."
        return [Output(type="text", payload=body, meta={"goal_runner": True})]

    if t.startswith("/goal_monitor"):
        st = _load(orchestrator, user_id, group_id)
        if not st:
            return [Output(type="text", payload="Goal Runner: нет активной задачи для мониторинга.", meta={"goal_runner": True})]
        return [Output(type="text", payload=monitor_progress(st), meta={"goal_runner": True})]

    st_wait = _load(orchestrator, user_id, group_id)
    if (
        st_wait
        and str(st_wait.get("status") or "") == "awaiting_clarify"
        and not t.startswith("/goal_cancel")
        and not t.startswith("/goal_status")
        and not t.startswith("/goal_run")
    ):
        if t.startswith("/goal_step"):
            return [
                Output(
                    type="text",
                    payload="Сначала ответьте на уточнение обычным текстом или /goal_cancel.",
                    meta={"goal_runner": True},
                )
            ]
        if t.startswith("/") and not t.startswith("/goal_"):
            return [
                Output(
                    type="text",
                    payload="Сейчас жду текстового ответа на уточнение. Или /goal_cancel.",
                    meta={"goal_runner": True},
                )
            ]
        return await _resume_after_clarify(orchestrator, user_id, group_id, st_wait, t)

    st_idle = _load(orchestrator, user_id, group_id)
    if auto_start_from_nl() and not _blocks_auto_new_goal(st_idle) and not t.startswith("/"):
        # GoalRunnerLearner: если история говорит skip — отдаём пайплайну
        learner = get_goal_runner_learner()
        if learner.should_skip(t):
            logger.debug("goal_runner: learner skip for %s", t[:80])
            return None

        # Классификация типа задачи (без LLM)
        task_type = classify_goal_runner_need(t)
        if task_type == TaskType.PURE_TEXT:
            logger.debug("goal_runner: pure_text task, skip (%s)", t[:80])
            learner.record_outcome(t, "unnecessary", duration_s=0.0)
            return None
        if task_type == TaskType.MULTISTEP_TEXT:
            logger.debug("goal_runner: multistep_text (no tools), skip (%s)", t[:80])
            learner.record_outcome(t, "unnecessary", duration_s=0.0)
            return None
        if task_type == TaskType.SIMPLE:
            logger.debug("goal_runner: simple task, skip (%s)", t[:80])
            learner.record_outcome(t, "unnecessary", duration_s=0.0)
            return None

        # Дальше только MULTISTEP_TOOL — нужен Goal Runner
        if warrants_multistep_goal_text(t):
            return await _start_goal_run_from_rest(orchestrator, user_id, group_id, t)
        if _auto_start_smart_llm_enabled() and len(t) >= _auto_start_smart_min_chars():
            if await _llm_classify_multistep_goal(orchestrator, t):
                return await _start_goal_run_from_rest(orchestrator, user_id, group_id, t)

    if t.startswith("/goal_run"):
        rest = t[len("/goal_run") :].strip()
        if not rest:
            return [
                Output(
                    type="text",
                    payload="Использование: /goal_run <формулировка цели>",
                    meta={"goal_runner": True},
                )
            ]
        return await _start_goal_run_from_rest(orchestrator, user_id, group_id, rest)

    st_pre = _load(orchestrator, user_id, group_id)
    if t.startswith("/goal_step"):
        if not st_pre:
            return [
                Output(
                    type="text",
                    payload=(
                        "Нет активной задачи. Опиши цель обычным текстом (несколько шагов) "
                        "или командой /goal_run …"
                    ),
                    meta={"goal_runner": True},
                )
            ]
    elif steal_turns() and st_pre and str(st_pre.get("status") or "") in {"running", "blocked"}:
        if t.startswith("/") and not t.startswith("/goal_"):
            return None
    else:
        return None

    out = await _execute_goal_steps(
        orchestrator,
        user_id,
        group_id,
        per_batch_tool_limit=_max_tools_per_message(),
        run_all=False,
    )
    return out
