"""
Сводка операторских правок для external_hint — всегда в slim-чате.

ephemeral_lessons_brain_addon часто не попадает в prompt_pack (include_ephemeral_lessons=false
у standard/quick_explain). Этот модуль дублирует критичное в external_hint.
"""
from __future__ import annotations

import logging

import os
import re
from typing import Any, Dict, List, Optional

import json
from pathlib import Path

from core.runtime_telegram_settings import effective_bool


logger = logging.getLogger(__name__)

def corrections_in_hint_enabled() -> bool:
    return effective_bool("BRAIN_OPERATOR_CORRECTIONS_IN_HINT", default=True)


def negative_rating_lesson_instruction(
    *,
    user_text: str,
    intent: str,
    module: str,
    correction_text: str,
) -> str:
    if correction_text.strip():
        return correction_text.strip()[:500]
    low = (user_text or "").lower()
    if re.search(r"(?i)(перевед|translate|на англ|на нем|auf deutsch)", low):
        return (
            "Пользователь недоволен переводом: дай только перевод целевым языком, "
            "без уточняющих вопросов и без объяснений, если фраза уже ясна."
        )
    if re.search(r"(?i)(уравнен|2x|реши|посчитай|калькулятор)", low):
        return (
            "Пользователь недоволен математикой: для уравнений — пошаговое решение и ответ; "
            "не подставляй голый результат калькулятора вместо решения."
        )
    if re.search(
        r"(?i)(кратк|без\s+рассужден|только\s+цифр|только\s+факт|не\s+развод|обрезал|обрезан)",
        low,
    ):
        return (
            "Пользователь просит краткий ответ: только итог по пунктам (цифры/1–2 фразы), "
            "без внутренних рассуждений, без «мы находимся…», без повторения условия."
        )
    if intent in ("news", "web", "search"):
        return "Пользователь недоволен: не выдумывай факты; кратко укажи источник или признай нехватку данных."
    return (
        "Пользователь поставил отрицательную оценку последнему ответу: "
        "исправь подход; не повторяй тот же шаблон; один уточняющий вопрос только если без него нельзя."
    )


def _negative_rating_lesson_instruction(
    *,
    user_text: str,
    intent: str,
    module: str,
    correction_text: str,
) -> str:
    return negative_rating_lesson_instruction(
        user_text=user_text,
        intent=intent,
        module=module,
        correction_text=correction_text,
    )


def lesson_trigger_from_user_text(user_text: str) -> tuple[str, bool]:
    """
    Короткий стабильный триггер для contains/regex (повтор похожего запроса ловит урок).
    Возвращает (trigger, use_regex).
    """
    ut = (user_text or "").strip()
    if len(ut) < 4:
        return "", False
    low = ut.lower()
    if re.search(r"(?i)(перевед|translate|на англ|на нем|auf deutsch)", low):
        return r"(?i)(перевед|translate|на англ|на нем|auf deutsch)", True
    if re.search(r"(?i)(уравнен|реши\s|посчитай|сколько\s+будет|\d+\s*[\*×/])", low):
        return r"(?i)(уравнен|реши\s|посчитай|сколько\s+будет)", True
    if re.search(r"(?i)(тессеракт|пентеракт|гиперкуб|\d+-мерн)", low):
        return r"(?i)(тессеракт|пентеракт|гиперкуб|\d+-мерн)", True
    if re.search(r"(?i)(habr\.com|стать[ьяю]\s+про|перескаж|суммариз)", low):
        return r"(?i)(habr\.com|перескаж|суммариз|стать)", True
    # Первые ~48 символов по границе слова — лучше, чем 120 символов целиком
    chunk = re.sub(r"\s+", " ", ut)[:72].strip()
    if len(chunk) >= 12:
        return chunk[:48], False
    return ut[: max(12, len(ut))], False


def _pending_turns_default() -> int:
    try:
        return max(1, min(20, int((os.getenv("USER_CORRECTION_PENDING_TURNS") or "6").strip())))
    except ValueError:
        return 6


def learning_ack_enabled() -> bool:
    return effective_bool("USER_LEARNING_ACK_ENABLED", default=True)


def format_learning_ack_message(
    applied: Optional[List[str]] = None,
    *,
    correction_text: str = "",
) -> str:
    """
    Короткое сообщение в Telegram: пользователь видит, что правка учтена.
    applied: элементы из record_user_correction_turn / apply_user_rating (ephemeral_lesson, pending_correction).
    """
    if not learning_ack_enabled():
        return ""
    acts = [str(x).strip() for x in (applied or []) if str(x).strip()]
    if not acts:
        return ""
    turns = _pending_turns_default()
    lines: List[str] = []
    if "pending_correction" in acts:
        lines.append(f"Учту вашу правку в следующих {turns} ответах.")
    if "ephemeral_lesson" in acts:
        lines.append("Добавил правило под похожие вопросы.")
    corr = (correction_text or "").strip()
    if corr and len(corr) > 6:
        lines.append(f"Заметка: {corr[:200]}")
    if not lines:
        return ""
    return "📝 " + " ".join(lines)


def format_trace_id_for_feedback(trace_id: str) -> str:
    """Короткий id для 👎 — поиск в turns_search / ops_trace."""
    tid = (trace_id or "").strip()
    if not tid:
        return ""
    short = tid if len(tid) <= 12 else tid[:12]
    return f"Трасса: <code>{short}</code> — <code>turns_search {short}</code>"


def format_learning_ack_from_rating(rep: Dict[str, Any]) -> str:
    if not isinstance(rep, dict) or not rep.get("ok"):
        return ""
    base = format_learning_ack_message(
        rep.get("applied") if isinstance(rep.get("applied"), list) else None,
        correction_text=str(rep.get("correction") or ""),
    )
    if int(rep.get("score") or 0) >= 0:
        return base
    tid = str(rep.get("trace_id") or "").strip()
    hint = format_trace_id_for_feedback(tid)
    if not hint:
        return base
    if not base:
        return hint
    return f"{base}\n{hint}"


def set_pending_user_correction(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
    *,
    instruction: str,
    user_excerpt: str,
    source: str = "rating",
) -> None:
    """Следующие N ходов — жёсткая подсказка в external_hint (даже без match ephemeral)."""
    if not behavior_store or not str(user_id or "").strip():
        return
    inst = (instruction or "").strip()
    if not inst:
        return
    try:
        rec = behavior_store.load(str(user_id), group_id)
        rp = dict(rec.get("routing_prefs") or {})
        rp["pending_correction"] = {
            "instruction": inst[:500],
            "user_excerpt": (user_excerpt or "")[:160],
            "turns_left": _pending_turns_default(),
            "source": source,
        }
        rec["routing_prefs"] = rp
        behavior_store.save(str(user_id), group_id, rec)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'user_correction_bus', e, exc_info=True)
def consume_pending_correction_hint(
    behavior_store: Any,
    user_id: str,
    group_id: Optional[str],
) -> str:
    """Вернуть блок для hint и уменьшить turns_left."""
    if not behavior_store or not str(user_id or "").strip():
        return ""
    try:
        rec = behavior_store.load(str(user_id), group_id)
        rp = dict(rec.get("routing_prefs") or {})
        pending = rp.get("pending_correction")
        if not isinstance(pending, dict):
            return ""
        left = int(pending.get("turns_left") or 0)
        if left <= 0:
            rp.pop("pending_correction", None)
            rec["routing_prefs"] = rp
            behavior_store.save(str(user_id), group_id, rec)
            return ""
        inst = str(pending.get("instruction") or "").strip()
        ex = str(pending.get("user_excerpt") or "").strip()
        pending["turns_left"] = left - 1
        if pending["turns_left"] <= 0:
            rp.pop("pending_correction", None)
        else:
            rp["pending_correction"] = pending
        rec["routing_prefs"] = rp
        behavior_store.save(str(user_id), group_id, rec)
        if not inst:
            return ""
        head = "Обязательная правка после негативной оценки пользователя (соблюдай строго):"
        if ex:
            return f"{head}\n- Контекст запроса: {ex}\n- {inst}"
        return f"{head}\n- {inst}"
    except Exception:
        return ""


def apply_negative_rating_lesson(
    *,
    user_id: str,
    user_text: str,
    intent: str,
    module: str,
    correction_text: str = "",
    source: str = "rating",
    behavior_rec: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
) -> bool:
    """Создать ephemeral lesson с триггером по anchor нити. Возвращает True если добавлено."""
    try:
        from core.ephemeral_lessons import add_lesson
        from core.feedback_contract import (
            build_rating_lesson_meta,
            rating_failure_class,
            rating_lesson_instruction,
            rating_lesson_trigger,
            resolve_anchor_user_q,
        )
    except Exception:
        return False
    ut = (user_text or "").strip()
    if len(ut) < 4:
        return False
    anchor = resolve_anchor_user_q(behavior_rec, ut)
    trig, use_regex = rating_lesson_trigger(rated_user_text=ut, anchor_user_q=anchor)
    if not trig:
        return False
    failure = rating_failure_class(
        ut, anchor, intent=intent or "", behavior_rec=behavior_rec
    )
    inst = rating_lesson_instruction(
        rated_user_text=ut,
        anchor_user_q=anchor,
        intent=intent or "",
        module=module or "",
        correction_text=correction_text or "",
        failure_class=failure,
    )
    meta = build_rating_lesson_meta(
        user_id=user_id,
        trace_id=trace_id,
        anchor_user_q=anchor,
        failure_class=failure,
        source=source,
        behavior_rec=behavior_rec,
    )
    try:
        add_lesson(
            trig,
            inst,
            match_regex=use_regex,
            meta=meta,
        )
        return True
    except Exception:
        return False


def record_user_correction_turn(
    *,
    user_id: str,
    user_text: str,
    behavior_store: Any = None,
    group_id: Optional[str] = None,
    correction_text: str = "",
    source: str = "dialogue_feedback",
) -> Dict[str, Any]:
    """
    «Не так» / 👎 без ожидания /rate: урок + pending на несколько ходов.
    Вызывать при user_feedback_likely на текущей реплике.
    """
    out: Dict[str, Any] = {"ok": False, "applied": []}
    uid = str(user_id or "").strip()
    if not uid:
        return out
    ctx: Dict[str, Any] = {}
    if behavior_store:
        try:
            from core.user_response_feedback import get_last_turn_context

            ctx = get_last_turn_context(behavior_store, uid, group_id)
        except Exception:
            ctx = {}
    last_user = str(ctx.get("last_user_excerpt") or "").strip()
    target_user = last_user or (user_text or "").strip()
    intent = str(ctx.get("last_intent") or "")
    module = str(ctx.get("last_module") or "")
    corr = (correction_text or user_text or "").strip()
    behavior_rec = None
    trace_id = str(ctx.get("last_trace_id") or "")
    if behavior_store:
        try:
            behavior_rec = behavior_store.load(uid, group_id)
        except Exception:
            behavior_rec = None
    try:
        from core.feedback_contract import (
            rating_failure_class,
            rating_lesson_instruction,
            resolve_anchor_user_q,
        )

        anchor = resolve_anchor_user_q(
            behavior_rec if isinstance(behavior_rec, dict) else None,
            target_user,
        )
        failure = rating_failure_class(
            target_user,
            anchor,
            intent=intent,
            behavior_rec=behavior_rec if isinstance(behavior_rec, dict) else None,
        )
        inst = rating_lesson_instruction(
            rated_user_text=target_user,
            anchor_user_q=anchor,
            intent=intent,
            module=module,
            correction_text=corr,
            failure_class=failure,
        )
    except Exception:
        inst = _negative_rating_lesson_instruction(
            user_text=target_user,
            intent=intent,
            module=module,
            correction_text=corr,
        )
    if apply_negative_rating_lesson(
        user_id=uid,
        user_text=target_user,
        intent=intent,
        module=module,
        correction_text=corr,
        source=source,
        behavior_rec=behavior_rec if isinstance(behavior_rec, dict) else None,
        trace_id=trace_id,
    ):
        out["applied"].append("ephemeral_lesson")
    if behavior_store:
        set_pending_user_correction(
            behavior_store,
            uid,
            group_id,
            instruction=inst,
            user_excerpt=target_user[:160],
            source=source,
        )
        out["applied"].append("pending_correction")
    out["ok"] = bool(out["applied"])
    return out


def _feedback_log_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "data" / "runtime" / "user_feedback.jsonl"


def recent_negative_feedback_hint(user_id: str, *, limit: int = 3) -> str:
    """Последние 👎/rate -1 для этого user_id — в external_hint (фаза 1 PRODUCT_FINISH)."""
    uid = str(user_id or "").strip()
    if not uid or limit <= 0:
        return ""
    p = _feedback_log_path()
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    hits: list[str] = []
    for line in reversed(lines):
        if len(hits) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("user_id") or "") != uid:
            continue
        if int(row.get("score") or 0) >= 0:
            continue
        excerpt = str(row.get("user_excerpt") or "").strip()[:120]
        corr = str(row.get("correction") or "").strip()[:200]
        intent = str(row.get("intent") or "").strip()
        if excerpt and corr:
            bit = f"{excerpt} — {corr}"
        else:
            bit = excerpt or corr or intent or "негативная оценка"
        hits.append(bit)
    if not hits:
        return ""
    bullets = "\n".join(f"- {h}" for h in reversed(hits))
    return (
        "Недавние негативные оценки этого пользователя (не повторяй тот же промах):\n"
        f"{bullets}"
    )


def build_operator_corrections_hint(
    context: Dict[str, Any],
    *,
    user_text: str = "",
    user_id: str = "",
) -> str:
    """
    Блок для external_hint: ephemeral + operator rules (кратко) + недавние 👎.
    Не дублирует user_remark_hint если он уже отдельно в pipeline — только ephemeral/operator.
    """
    if not corrections_in_hint_enabled():
        return ""
    if not isinstance(context, dict):
        return ""
    try:
        from core.turn_decision_spine import ephemeral_lessons_hint_for_context
    except Exception as e:
        logger.debug('%s optional failed: %s', 'user_correction_bus', e, exc_info=True)
        ephemeral_lessons_hint_for_context = None  # type: ignore[assignment,misc]
    parts = []
    if user_id:
        try:
            bs = context.get("_behavior_store")
            if bs is not None:
                gid = context.get("group_id")
                pending = consume_pending_correction_hint(bs, str(user_id), gid)
                if pending:
                    parts.append(pending)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'user_correction_bus', e, exc_info=True)
    ep = ""
    if ephemeral_lessons_hint_for_context is not None:
        ep = ephemeral_lessons_hint_for_context(context, user_text)
    if ep:
        parts.append(ep)
    # operator_rules_brain_addon часто длинный — в slim только если нет ephemeral
    if not ep:
        op = str(context.get("operator_rules_brain_addon") or "").strip()
        if op and len(op) < 1200:
            parts.append(op)
    neg_fb = recent_negative_feedback_hint(user_id, limit=3)
    if neg_fb:
        parts.append(neg_fb)
    return "\n\n".join(parts).strip()
