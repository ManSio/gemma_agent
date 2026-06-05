"""
Фоновая перепроверка каждого хода: маршрут, ответ, контракт search/pivot.

Слушает turn.outcome + maintenance.tick. Не блокирует Telegram.
Не меняет .env и не auto-apply MCE — только логи, route_risk, pending_correction.

См. docs/TURN_QUALITY_LOOP_RU.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.event_bus import bus
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

_INSTALLED = False


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def turn_quality_loop_enabled() -> bool:
    return _truthy("TURN_QUALITY_LOOP_ENABLED", False)


def _auto_pending_correction() -> bool:
    return _truthy("TURN_QUALITY_AUTO_PENDING_CORRECTION", False)


def _commerce_only_learning() -> bool:
    return _truthy("TURN_QUALITY_COMMERCE_ONLY_LEARNING", True)


def _lesson_draft_enabled() -> bool:
    return _truthy("TURN_QUALITY_LESSON_DRAFT", True)


def _should_skip_quality_loop(payload: Dict[str, Any]) -> bool:
    """E2: не учить на probe/route_only ходах (шум в lesson_draft)."""
    if not _truthy("TURN_QUALITY_SKIP_PROBE", True):
        return False
    uid = str(payload.get("user_id") or "").strip()
    if not uid:
        return False
    exact = (os.getenv("QUALITY_LOOP_SKIP_PROBE_UID") or "").strip()
    if exact and uid == exact:
        return True
    raw_prefixes = (os.getenv("QUALITY_LOOP_SKIP_UID_PREFIXES") or "probe_,test_").strip()
    for pref in raw_prefixes.split(","):
        p = pref.strip()
        if p and uid.startswith(p):
            return True
    lane = str(payload.get("dialogue_lane") or "").strip().lower()
    if lane in ("route_only", "probe"):
        return True
    return False


_COMMERCE_ISSUES = frozenset({"search_skipped", "price_hallucination", "wrong_route_clarify"})
_GENERAL_QUALITY_ISSUES = frozenset({"topic_drift", "reply_echo", "bot_scope_leak"})


def _had_news_scenario(payload: Dict[str, Any]) -> bool:
    sc = payload.get("scenario_hits")
    if not isinstance(sc, list):
        return False
    for h in sc:
        if isinstance(h, dict) and str(h.get("id") or "") == "news_turn":
            return True
    return False


def _scrub_false_search_skipped(
    issues: List[str],
    user_text: str,
    payload: Dict[str, Any],
) -> List[str]:
    if "search_skipped" not in issues:
        return issues
    from core.product_behavior import price_or_commerce_search_required

    if not price_or_commerce_search_required(user_text):
        return [i for i in issues if i != "search_skipped"]
    intent = str(payload.get("intent") or "").lower()
    if intent in ("news", "news_brief", "chitchat"):
        return [i for i in issues if i != "search_skipped"]
    if _had_news_scenario(payload):
        return [i for i in issues if i != "search_skipped"]
    return issues


def _learning_action_issues(issues: List[str], user_text: str) -> List[str]:
    """Issues that may trigger pending_correction / ephemeral / lesson_draft."""
    if not issues:
        return []
    if not _commerce_only_learning():
        return list(issues)
    from core.product_behavior import price_or_commerce_search_required

    out: List[str] = []
    for tag in issues:
        if tag == "reply_echo":
            try:
                from core.product_behavior import should_skip_reply_echo_for_user_text

                if should_skip_reply_echo_for_user_text(user_text):
                    continue
            except Exception:
                pass
        if tag in _GENERAL_QUALITY_ISSUES:
            out.append(tag)
        elif tag in _COMMERCE_ISSUES:
            if tag == "search_skipped" and not price_or_commerce_search_required(user_text):
                continue
            out.append(tag)
    return out


def _runtime_dir() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "data" / "runtime"


def _audit_log_path() -> Path:
    raw = (os.getenv("TURN_QUALITY_AUDIT_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _runtime_dir() / "quality_audit.jsonl"


def _lessons_path() -> Path:
    return _runtime_dir() / "agent_test_lessons.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.debug("turn_quality append %s: %s", path, e)


def _jsonl_recent_keys(path: Path, *, limit: int = 500) -> set[tuple[str, ...]]:
    """Ключи недавних строк — чтобы не плодить один и тот же урок/аудит сотни раз."""
    if not path.is_file():
        return set()
    keys: set[tuple[str, ...]] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    for line in lines[-limit:]:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        excerpt = str(o.get("user_excerpt") or o.get("user_text") or "")[:120]
        issues = tuple(sorted(str(i) for i in (o.get("issues") or o.get("errors") or []) if i))
        uid = str(o.get("user_id") or "")
        fp = str(o.get("fp") or "")
        keys.add((uid, fp, excerpt, issues))
    return keys


def _audit_row_key(audit: Dict[str, Any], payload: Dict[str, Any]) -> tuple[str, ...]:
    issues = tuple(sorted(str(i) for i in (audit.get("issues") or []) if i))
    excerpt = str(audit.get("user_excerpt") or "")[:120]
    uid = str(audit.get("user_id") or "")
    fp = str(payload.get("fp") or "")
    return (uid, fp, excerpt, issues)


def _last_assistant_before_turn(user_id: str, group_id: Optional[str]) -> str:
    if not user_id:
        return ""
    try:
        from core.behavior_store import BehaviorStore

        rec = BehaviorStore().load(user_id, group_id)
        rows = rec.get("recent_messages") or []
        seen_user = False
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "").lower()
            text = str(row.get("text") or row.get("content") or "").strip()
            if role == "user" and text:
                if seen_user:
                    break
                seen_user = True
                continue
            if role == "assistant" and text and seen_user:
                return text
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if str(row.get("role") or "").lower() == "assistant":
                return str(row.get("text") or row.get("content") or "").strip()
    except Exception as e:
        logger.debug("turn_quality last_assistant: %s", e)
    return ""


def _instruction_for_issues(issues: List[str], user_excerpt: str) -> str:
    parts: List[str] = []
    if "topic_drift" in issues:
        parts.append(
            "Не отвечай на новый вопрос (наука/общее) текстом про прошлую тему (товар, цены, магазины)."
        )
    if "reply_echo" in issues:
        parts.append("Не повторяй дословно прошлый ответ ассистента — ответь на текущую реплику.")
    if "search_skipped" in issues or "price_hallucination" in issues:
        parts.append(
            "На цены/магазины/«найди …» — сначала UniversalSearch с country из user_facts; "
            "без выдачи не указывай ₽/BYN и ссылки."
        )
    if "wrong_route_clarify" in issues:
        parts.append(
            "На уточнение по ценам/магазинам не уходи в clarify «чем помочь» — выполни поиск."
        )
    if not parts:
        parts.append(f"Исправь ответ на реплику: {user_excerpt[:120]}")
    return " ".join(parts)[:480]


def audit_turn_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Пост-ходовой аудит (не блокирует ответ пользователю)."""
    from core.product_behavior import (
        assistant_reply_issues,
        should_force_product_search,
        price_or_commerce_search_required,
        subject_bucket,
    )
    from core.turn_observer import detect_issues

    user_text = str(payload.get("user_excerpt") or "").strip()
    assistant = str(payload.get("assistant_excerpt") or "").strip()
    uid = str(payload.get("user_id") or "").strip()
    gid = payload.get("group_id")

    issues: List[str] = list(
        detect_issues(
            outcome=str(payload.get("outcome") or ""),
            user_feedback_negative=bool(payload.get("user_feedback_negative")),
            user_feedback_positive=bool(payload.get("user_feedback_positive")),
            assistant_excerpt=assistant,
            detail=str(payload.get("detail") or ""),
        )
    )

    last_asst = _last_assistant_before_turn(uid, gid if gid is not None else None)
    for tag in assistant_reply_issues(user_text, assistant, last_asst):
        if tag not in issues:
            issues.append(tag)

    outcome = str(payload.get("outcome") or "").strip().lower()
    if should_force_product_search(user_text):
        if outcome == "clarify":
            if "wrong_route_clarify" not in issues:
                issues.append("wrong_route_clarify")
        sc = payload.get("scenario_hits")
        had_search = False
        if isinstance(sc, list):
            for h in sc:
                if isinstance(h, dict) and "search" in str(h.get("id") or "").lower():
                    had_search = True
        if not had_search and outcome in ("ok", "success", "resolved"):
            low_a = assistant.lower()
            if any(x in low_a for x in ("₽", "руб", "mts", "samsung", "mobistore", "byn")):
                if "price_hallucination" not in issues:
                    issues.append("price_hallucination")
            elif price_or_commerce_search_required(user_text) and "search_skipped" not in issues:
                issues.append("search_skipped")

    if (
        subject_bucket(user_text) == "science"
        and subject_bucket(last_asst or assistant) == "commerce"
        and outcome == "ok"
        and "topic_drift" not in issues
    ):
        issues.append("topic_drift")

    issues = _scrub_false_search_skipped(issues, user_text, payload)

    return {
        "issues": issues,
        "user_id": uid,
        "group_id": gid,
        "user_excerpt": user_text,
        "assistant_excerpt": assistant[:240],
        "intent": str(payload.get("intent") or ""),
        "module": str(payload.get("module") or ""),
        "profile": str(payload.get("profile") or ""),
        "outcome": outcome,
    }


def _apply_learning_actions(audit: Dict[str, Any], payload: Dict[str, Any]) -> List[str]:
    """route_risk + pending_correction + черновик урока. Без MCE auto_apply."""
    actions: List[str] = []
    issues = audit.get("issues") or []
    learn_issues = _learning_action_issues(issues, str(audit.get("user_excerpt") or ""))
    if not learn_issues:
        return actions

    uid = str(audit.get("user_id") or "").strip()
    user_text = str(audit.get("user_excerpt") or "").strip()

    try:
        from core.route_risk_memory import record_stumble

        record_stumble(
            user_text=user_text or "(quality)",
            intent=str(audit.get("intent") or ""),
            module=str(audit.get("module") or ""),
            outcome="failure",
            detail="quality_loop:" + ",".join(learn_issues)[:100],
            skill_name=str(audit.get("skill") or ""),
        )
        actions.append("route_risk")
    except Exception as e:
        logger.debug("turn_quality route_risk: %s", e)

    if _auto_pending_correction() and uid and user_text:
        try:
            from core.behavior_store import BehaviorStore
            from core.user_correction_bus import (
                apply_negative_rating_lesson,
                set_pending_user_correction,
            )

            inst = _instruction_for_issues(learn_issues, user_text)
            bs = BehaviorStore()
            if apply_negative_rating_lesson(
                user_id=uid,
                user_text=user_text,
                intent=str(audit.get("intent") or ""),
                module=str(audit.get("module") or ""),
                correction_text=inst,
                source="quality_loop",
            ):
                actions.append("ephemeral_lesson")
            set_pending_user_correction(
                bs,
                uid,
                audit.get("group_id"),
                instruction=inst,
                user_excerpt=user_text[:160],
                source="quality_loop",
            )
            actions.append("pending_correction")
        except Exception as e:
            logger.debug("turn_quality correction: %s", e)

    if _lesson_draft_enabled():
        draft_key = _audit_row_key(audit, payload)
        recent = _jsonl_recent_keys(_lessons_path())
        if draft_key not in recent:
            _append_jsonl(
                _lessons_path(),
                {
                    "ts": _now_iso(),
                    "source": "quality_loop",
                    "user_id": uid,
                    "issues": learn_issues,
                    "user_excerpt": user_text[:160],
                    "hint": _instruction_for_issues(learn_issues, user_text),
                    "intent": audit.get("intent"),
                    "module": audit.get("module"),
                    "fp": payload.get("fp"),
                },
            )
            actions.append("lesson_draft")

    return actions


def _log_heuristic_miss_on_bad_shortcut(payload: Dict[str, Any], issues: List[str]) -> bool:
    """C1+: если сработал shortcut и ход помечен quality_loop — записать miss для review."""
    if not issues:
        return False
    try:
        from core.memory_ops_report import shortcut_rule_id_from_turn_payload
        from core.heuristic_misses_log import record_heuristic_miss

        rid = shortcut_rule_id_from_turn_payload(payload)
        if not rid:
            return False
        user_text = str(payload.get("user_excerpt") or "").strip()
        topic = str(payload.get("topic_current") or "").strip()
        record_heuristic_miss(
            rule_id=rid,
            verdict="blocked",
            reason="quality_loop:" + ",".join(issues)[:120],
            user_text=user_text,
            topic_current=topic,
            user_id=str(payload.get("user_id") or "").strip(),
        )
        return True
    except Exception as e:
        logger.debug("turn_quality heuristic_miss: %s", e)
        return False


async def process_turn_outcome(payload: Dict[str, Any]) -> None:
    if not turn_quality_loop_enabled() or not isinstance(payload, dict):
        return
    if _should_skip_quality_loop(payload):
        return
    try:
        audit = audit_turn_payload(payload)
        issues = audit.get("issues") or []
        if not issues:
            return
        actions = _apply_learning_actions(audit, payload)
        if _log_heuristic_miss_on_bad_shortcut(payload, issues):
            actions.append("heuristic_miss")
        row = {
            "ts": _now_iso(),
            "user_id": audit.get("user_id"),
            "group_id": audit.get("group_id"),
            "fp": payload.get("fp"),
            "intent": audit.get("intent"),
            "module": audit.get("module"),
            "profile": audit.get("profile"),
            "outcome": audit.get("outcome"),
            "issues": issues,
            "actions": actions,
            "user_excerpt": audit.get("user_excerpt"),
            "assistant_excerpt": audit.get("assistant_excerpt"),
        }
        audit_key = _audit_row_key(audit, payload)
        if audit_key not in _jsonl_recent_keys(_audit_log_path()):
            _append_jsonl(_audit_log_path(), row)
        MONITOR.inc("turn_quality_issues_total")
        for tag in issues:
            MONITOR.inc(f"turn_quality_issue_{tag}_total")
        logger.info(
            "turn_quality issues=%s actions=%s user=%s",
            issues,
            actions,
            audit.get("user_id"),
            extra={"gemma_event": "turn_quality", "issues": issues},
        )
        try:
            bus.emit(
                "quality.turn_flagged",
                {
                    "user_id": audit.get("user_id"),
                    "issues": issues,
                    "actions": actions,
                },
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'turn_quality_loop', e, exc_info=True)
    except Exception as e:
        logger.debug("turn_quality process: %s", e)


def _on_turn_outcome_sync(payload: Dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(process_turn_outcome(payload))
    except RuntimeError:
        asyncio.run(process_turn_outcome(payload))


def _scan_max_age_sec() -> int:
    try:
        return max(60, int((os.getenv("TURN_QUALITY_SCAN_MAX_AGE_SEC") or "3600").strip()))
    except ValueError:
        return 3600


def _recently_audited_fps(limit: int = 500) -> set[str]:
    """Не переаудировать те же ходы на maintenance.tick (шум Samsung/reply_echo)."""
    path = _audit_log_path()
    if not path.is_file():
        return set()
    fps: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    for line in lines[-limit:]:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        fp = str(o.get("fp") or "").strip()
        if fp:
            fps.add(fp)
    return fps


def _turn_age_sec(row: Dict[str, Any]) -> Optional[float]:
    ts = str(row.get("ts") or "").strip()
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return None


async def scan_recent_turns_file(*, limit: int = 40) -> Dict[str, Any]:
    """Периодический catch-up по turns.jsonl (maintenance.tick)."""
    from core.turn_observer import log_path, read_recent_turns

    if not turn_quality_loop_enabled():
        return {"scanned": 0, "flagged": 0}
    rows = read_recent_turns(limit=limit)
    audited = _recently_audited_fps()
    max_age = _scan_max_age_sec()
    flagged = 0
    skipped_fp = 0
    skipped_age = 0
    for row in rows:
        if row.get("type") == "scenario":
            continue
        if row.get("issues"):
            continue
        fp = str(row.get("fp") or "").strip()
        if fp and fp in audited:
            skipped_fp += 1
            continue
        age = _turn_age_sec(row)
        if age is not None and age > max_age:
            skipped_age += 1
            continue
        payload = {
            "user_excerpt": row.get("user_excerpt"),
            "assistant_excerpt": row.get("assistant_excerpt"),
            "user_id": row.get("user_id"),
            "group_id": row.get("group_id"),
            "intent": row.get("intent"),
            "module": row.get("module"),
            "profile": row.get("profile"),
            "outcome": row.get("outcome"),
            "detail": row.get("detail"),
            "fp": fp,
            "scenario_hits": row.get("scenario_hits"),
            "user_feedback_negative": False,
            "user_feedback_positive": False,
            "ok": row.get("ok"),
        }
        audit = audit_turn_payload(payload)
        if audit.get("issues"):
            flagged += 1
            await process_turn_outcome(payload)
            if fp:
                audited.add(fp)
    return {
        "scanned": len(rows),
        "flagged": flagged,
        "skipped_fp": skipped_fp,
        "skipped_age": skipped_age,
        "turns_path": str(log_path()),
    }


async def _on_maintenance_tick(_payload: Dict[str, Any]) -> None:
    if not _truthy("TURN_QUALITY_SCAN_ON_TICK", True):
        return
    try:
        lim = int((os.getenv("TURN_QUALITY_SCAN_LIMIT") or "30").strip() or "30")
    except ValueError:
        lim = 30
    stats = await scan_recent_turns_file(limit=max(5, lim))
    if stats.get("flagged"):
        logger.info("turn_quality scan flagged=%s", stats.get("flagged"))


def install_turn_quality_loop() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    bus.subscribe("turn.outcome", _on_turn_outcome_sync)
    bus.subscribe_async("maintenance.tick", _on_maintenance_tick)
    logger.info("turn_quality_loop installed")
