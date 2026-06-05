"""
Память успешных стратегий (путь шагов lookahead + уровень задачи).
Подмешивание при многоуровневых запросах или при совпадении отпечатка задачи.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from core.dialogue_plot_signals import plot_twist_likely
from core.experience_memory import fingerprint, normalize_module_key
from core.runtime_telegram_settings import effective_bool
from core.task_depth import infer_task_tier, tier_prefers_thorough

logger = logging.getLogger(__name__)

_STRATEGY_LOCK = threading.Lock()


def strategy_path_enabled() -> bool:
    return effective_bool("STRATEGY_PATH_MEMORY_ENABLED", default=True)


def default_store_path() -> str:
    p = (os.getenv("GEMMA_STRATEGY_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "strategy_paths.jsonl")


def _steps_summary(lookahead_plan: Dict[str, Any]) -> str:
    steps = lookahead_plan.get("steps") if isinstance(lookahead_plan.get("steps"), list) else []
    parts: List[str] = []
    for s in steps[:6]:
        if isinstance(s, dict):
            d = str(s.get("do") or "").strip()
            if d:
                parts.append(d)
    return " → ".join(parts)[:520]


def _strategy_sig(lookahead_plan: Dict[str, Any]) -> str:
    raw = _steps_summary(lookahead_plan)
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _trim_file_keep_tail(path: str, max_lines: int) -> None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[-max_lines:])
    except OSError as e:
        logger.debug("strategy_path trim: %s", e)


def _last_strategy_is_duplicate(store: str, fp: str, intent: str, summary: str) -> bool:
    """Проверить, что последняя запись для этого fp+intent уже имеет такую же steps_summary."""
    if not summary:
        return False
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, FileNotFoundError):
        return False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if str(rec.get("fp") or "") != fp:
            continue
        if str(rec.get("intent") or "") != (intent or "").strip():
            continue
        # Нашли последнюю запись для этого fp+intent
        return str(rec.get("steps_summary") or "") == summary
    return False


def append_strategy_success(
    *,
    user_text: str,
    intent: str,
    module: str,
    task_tier: str,
    lookahead_plan: Dict[str, Any],
    assistant_excerpt: str,
    path: Optional[str] = None,
    skill_name: str = "",
) -> None:
    if not strategy_path_enabled():
        return
    fp = fingerprint(user_text)
    if not fp or not (assistant_excerpt or "").strip():
        return
    try:
        from core.brain.text_helpers import is_bot_operational_diag_reply

        if is_bot_operational_diag_reply(assistant_excerpt):
            return
    except Exception as e:
        logger.debug('%s optional failed: %s', 'strategy_path_memory', e, exc_info=True)
    summary = _steps_summary(lookahead_plan)
    if not summary:
        return
    store = path or default_store_path()
    # Дедупликация: если последний сохранённый путь для того же fp+intent
    # уже имеет такую же steps_summary — не пишем дубль.
    if _last_strategy_is_duplicate(store, fp, intent, summary):
        return
    try:
        os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    except OSError:
        pass
    tier = (task_tier or infer_task_tier(user_text)).strip() or "shallow"
    path_style = "long" if tier_prefers_thorough(tier) else "short"
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "fp": fp,
        "intent": (intent or "").strip() or "unknown",
        "skill": (skill_name or "").strip() or None,
        "module": normalize_module_key(module),
        "task_tier": tier,
        "path_style": path_style,
        "strategy_sig": _strategy_sig(lookahead_plan),
        "steps_summary": summary,
        "assistant_excerpt": (assistant_excerpt or "").strip()[:360],
    }
    try:
        with _STRATEGY_LOCK:
            with open(store, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
    except OSError as e:
        logger.debug("strategy_path append: %s", e)
        return
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, set_json

        if agent_kv_enabled():
            try:
                sttl = max(3600, int((os.getenv("AGENT_KV_STRATEGY_TTL_SEC") or "7776000").strip() or "7776000"))
            except ValueError:
                sttl = 7776000
            sk = f'{fp}|{(intent or "").strip() or "unknown"}'
            blob = {
                **rec,
                "lookahead_plan": lookahead_plan if isinstance(lookahead_plan, dict) else {},
            }
            set_json("strategy", sk, blob, branch=agent_kv_branch(), ttl_sec=sttl, priority=30)
    except Exception as e:
        logger.debug("strategy_path kv: %s", e)
    try:
        from core.skill_store import auto_crystallize as _auto_x
        _auto_x(
            fp=fp,
            intent=intent,
            module=module,
            steps_summary=summary,
            assistant_excerpt=(assistant_excerpt or "").strip()[:360],
        )
    except Exception as e:
        logger.debug("strategy_path auto-crystallize: %s", e)
    try:
        max_lines = int((os.getenv("STRATEGY_PATH_MAX_LINES") or "6000").strip() or "6000")
        if max_lines > 0 and os.path.isfile(store) and os.path.getsize(store) > 1_800_000:
            _trim_file_keep_tail(store, max_lines)
    except (OSError, ValueError):
        pass


def _iter_reverse(path: str) -> Iterator[Dict[str, Any]]:
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


def _tier_compatible(need: str, stored: str) -> bool:
    if need == stored:
        return True
    if need == "deep" and stored in ("deep", "nested"):
        return True
    if need == "nested" and stored in ("nested", "shallow"):
        return True
    return False


def build_strategy_path_hint(
    *,
    user_text: str,
    intent: str,
    task_tier: str,
    path: Optional[str] = None,
) -> str:
    if not strategy_path_enabled():
        return ""
    if plot_twist_likely(user_text):
        return ""
    fp = fingerprint(user_text)
    if not fp:
        return ""
    tier = (task_tier or infer_task_tier(user_text)).strip() or "shallow"
    want_int = (intent or "").strip() or "unknown"
    store = path or default_store_path()
    attach_for = effective_bool("STRATEGY_PATH_HINT_FOR_SHALLOW", default=False)
    try:
        lookback = max(30, int((os.getenv("STRATEGY_PATH_LOOKBACK") or "160").strip() or "160"))
    except ValueError:
        lookback = 160
    best: Optional[Dict[str, Any]] = None
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json

        if agent_kv_enabled():
            row = get_json("strategy", f"{fp}|{want_int}", branch=agent_kv_branch())
            if isinstance(row, dict) and str(row.get("steps_summary") or "").strip():
                st = str(row.get("task_tier") or "shallow")
                if _tier_compatible(tier, st):
                    summ = str(row.get("steps_summary") or "").strip()
                    ps = str(row.get("path_style") or "")
                    return (
                        "(Память стратегии: по этой же задаче уже срабатывал удачный путь — ориентир по шагам, не копируй дословно.)\n"
                        f"Уровень: {tier}; прошлый стиль пути: {ps}.\n"
                        f"Путь: {summ}"
                    )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'strategy_path_memory', e, exc_info=True)
    if tier == "shallow" and not attach_for:
        return ""
    n = 0
    for rec in _iter_reverse(store):
        n += 1
        if n > lookback:
            break
        if str(rec.get("fp") or "") != fp:
            continue
        if str(rec.get("intent") or "") != want_int:
            continue
        st = str(rec.get("task_tier") or "shallow")
        if not _tier_compatible(tier, st):
            continue
        best = rec
        break
    if not best:
        return ""
    summ = str(best.get("steps_summary") or "").strip()
    ps = str(best.get("path_style") or "")
    if not summ:
        return ""
    line = (
        "(Память стратегии: по этой же задаче уже срабатывал удачный путь — ориентир по шагам, не копируй дословно.)\n"
        f"Уровень: {tier}; прошлый стиль пути: {ps}.\n"
        f"Путь: {summ}"
    )
    return line
