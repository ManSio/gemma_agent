"""
Память исходов в JSONL: успешные ответы + (опционально) неудачи/уточнения.
Подсказки в промпт: позитив при «неуверенном» роутинге; негатив — по умолчанию всегда, если есть совпадение по отпечатку.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_INT_RE = re.compile(r"^\s*[-+]?\d+\s*$")
_NUM_RE = re.compile(r"^\s*[-+]?\d+(?:[.,]\d+)?\s*$")
_N_SENT_RE = re.compile(r"(?i)\bв\s+(\d+)\s+предложени")
_ONLY_NUMBER_REQ_RE = re.compile(r"(?i)(только\s+число|ответ\s*[:\-]?\s*только\s+число|выведи\s+только\s+число)")
_NOISE_RUN_RE = re.compile(r"(.)\1{11,}", re.DOTALL)
# Ответ ассистента похож на запрос уточнения (для self_model / CDC / опыта).
_CLARIFY_HINT_RE = re.compile(
    r"(?i)(уточн(?:и|ите|ить)|что\s+именно|какой\s+именно|что\s+ты\s+имеешь\s+в\s+виду|что\s+имеете?\s+в\s+виду|"
    r"какой\s+аспект|о\s+чём\s+речь|не\s+(?:совсем\s+)?понятн[оа]|можешь\s+(?:уточнить|пояснить)|"
    r"could\s+you\s+clarify|what\s+do\s+you\s+mean|which\s+(?:one|aspect|option)\b)"
)
# Короткий запрос пользователя + ответ с меню направлений («с какого момента…», «что из трёх…»).
_DISAMBIG_MENU_RE = re.compile(
    r"(?i)(с\s+какого\s+момента|с\s+чего\s+(?:начн|продолж)|"
    r"продолжим:\s|продолжим\s+\(|"
    r"что\s+(?:сначала|в\s+первую\s+очередь)\s*\?|"
    r"какой\s+(?:из\s+)?(?:трёх|трех|этих|вариантов)\s|"
    r"куда\s+дальше|с\s+чего\s+нам\s+|"
    r"or\s+(?:the\s+)?(?:first|second|third)\s+—|which\s+should\s+we)"
)
# Длинный ответ, в конце вилка «хочешь … или …?» (короткий запрос пользователя обрабатывается выше).
_FORK_CHOICE_RE = re.compile(
    r"(?i)(?:"
    r"(хочешь|хотите|можем\s+ли)\b[\s,]+[^?]{5,1200}\bили\b[^?]{4,}\?"
    r"|(?:want\s+me\s+to|should\s+i)\b[\s,]+[^?]{5,800}\bor\b[^?]{4,}\?"
    r")\s*\Z"
)

_BAD_OUTCOMES = frozenset({"clarify", "failure", "error", "fallback"})


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def experience_enabled() -> bool:
    return _env_truthy("EXPERIENCE_MEMORY_ENABLED", True)


def experience_negative_recording_enabled() -> bool:
    """Запись не-ok исходов в тот же JSONL (самообучение на ошибках)."""
    return experience_enabled() and _env_truthy("EXPERIENCE_NEGATIVE_RECORDING_ENABLED", True)


def experience_negative_hint_enabled() -> bool:
    """Подмешивание предупреждений по прошлым не-ok для того же отпечатка запроса."""
    return experience_enabled() and _env_truthy("EXPERIENCE_NEGATIVE_HINT_ENABLED", True)


def normalize_module_key(name: str) -> str:
    return (name or "").strip().replace("-", "_").lower()


def normalize_user_text(text: str) -> str:
    s = (text or "").strip().lower()
    s = _WS_RE.sub(" ", s)
    return s


def fingerprint(text: str) -> str:
    """Стабильный короткий отпечаток пользовательского текста."""
    norm = normalize_user_text(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def default_store_path() -> str:
    custom = (os.getenv("GEMMA_EXPERIENCE_PATH") or "").strip()
    if custom:
        return custom
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "experience_digest.jsonl")


def _trim_file_keep_tail(path: str, max_lines: int) -> None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    keep = lines[-max_lines:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except OSError as e:
        logger.debug("experience_memory trim: %s", e)


def append_success(
    *,
    user_text: str,
    intent: str,
    module: str,
    planner_reason: str,
    assistant_excerpt: str,
    path: Optional[str] = None,
    skill_name: str = "",
) -> None:
    if not experience_enabled():
        return
    fp = fingerprint(user_text)
    if not fp or not (assistant_excerpt or "").strip():
        return
    try:
        from core.brain.text_helpers import is_bot_operational_diag_reply

        if is_bot_operational_diag_reply(assistant_excerpt):
            return
    except Exception as e:
        logger.debug('%s optional failed: %s', 'experience_memory', e, exc_info=True)
    store = path or default_store_path()
    try:
        os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    except OSError:
        pass
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "fp": fp,
        "intent": (intent or "").strip() or "unknown",
        "skill": (skill_name or "").strip() or None,
        "module": normalize_module_key(module),
        "planner_reason": (planner_reason or "")[:240],
        "outcome": "ok",
        "user_excerpt": (user_text or "").strip()[:200],
        "assistant_excerpt": (assistant_excerpt or "").strip()[:480],
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with open(store, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.debug("experience_memory append: %s", e)
        return
    try:
        max_lines = int((os.getenv("EXPERIENCE_MAX_LINES") or "4000").strip() or "4000")
        max_bytes = int((os.getenv("EXPERIENCE_MAX_FILE_BYTES") or "2097152").strip() or "2097152")
        if max_lines > 0 and os.path.getsize(store) > max(65536, max_bytes):
            _trim_file_keep_tail(store, max_lines)
    except (OSError, ValueError):
        pass


def append_experience_record(
    *,
    user_text: str,
    intent: str,
    module: str,
    planner_reason: str,
    outcome: str,
    assistant_excerpt: str = "",
    detail: str = "",
    path: Optional[str] = None,
    skill_name: str = "",
) -> None:
    """
    Дополняет журнал записями с исходом не ok (clarify, failure, error, fallback).
    Отключается EXPERIENCE_NEGATIVE_RECORDING_ENABLED=false.
    """
    if not experience_negative_recording_enabled():
        return
    oc = (outcome or "").strip().lower()
    if oc not in _BAD_OUTCOMES:
        return
    fp = fingerprint(user_text)
    if not fp:
        return
    ex = (assistant_excerpt or "").strip()
    if not ex and oc not in {"error", "fallback"}:
        return
    try:
        from core.brain.text_helpers import is_bot_operational_diag_reply

        if ex and is_bot_operational_diag_reply(ex):
            return
    except Exception as e:
        logger.debug('%s optional failed: %s', 'experience_memory', e, exc_info=True)
    store = path or default_store_path()
    try:
        os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    except OSError:
        pass
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "fp": fp,
        "intent": (intent or "").strip() or "unknown",
        "skill": (skill_name or "").strip() or None,
        "module": normalize_module_key(module),
        "planner_reason": (planner_reason or "")[:240],
        "outcome": oc,
        "user_excerpt": (user_text or "").strip()[:200],
        "assistant_excerpt": ex[:480] if ex else "",
        "detail": (detail or "").strip()[:240],
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with open(store, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.debug("experience_memory append_experience_record: %s", e)
        return
    try:
        max_lines = int((os.getenv("EXPERIENCE_MAX_LINES") or "4000").strip() or "4000")
        max_bytes = int((os.getenv("EXPERIENCE_MAX_FILE_BYTES") or "2097152").strip() or "2097152")
        if max_lines > 0 and os.path.getsize(store) > max(65536, max_bytes):
            _trim_file_keep_tail(store, max_lines)
    except (OSError, ValueError):
        pass


def _iter_records_reverse(path: str) -> Iterator[Dict[str, Any]]:
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
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def find_hints(
    *,
    user_text: str,
    intent: str,
    module: str,
    path: Optional[str] = None,
    limit: int = 2,
) -> List[str]:
    if not experience_enabled():
        return []
    fp = fingerprint(user_text)
    if not fp:
        return []
    want_int = (intent or "").strip() or "unknown"
    want_mod = normalize_module_key(module)
    store = path or default_store_path()
    hints: List[str] = []
    for rec in _iter_records_reverse(store):
        if str(rec.get("outcome") or "") != "ok":
            continue
        if str(rec.get("intent") or "") != want_int:
            continue
        if str(rec.get("module") or "") != want_mod:
            continue
        rfp = str(rec.get("fp") or "")
        if rfp != fp:
            continue
        ex = str(rec.get("assistant_excerpt") or "").strip()
        if ex and ex not in hints:
            hints.append(ex)
        if len(hints) >= limit:
            break
    return hints


def find_negative_hints(
    *,
    user_text: str,
    intent: str,
    module: str,
    path: Optional[str] = None,
    limit: int = 2,
) -> List[str]:
    if not experience_enabled():
        return []
    fp = fingerprint(user_text)
    if not fp:
        return []
    want_int = (intent or "").strip() or "unknown"
    want_mod = normalize_module_key(module)
    store = path or default_store_path()
    try:
        lim = int((os.getenv("EXPERIENCE_NEGATIVE_HINT_LIMIT") or str(limit)).strip() or str(limit))
    except ValueError:
        lim = limit
    lim = max(1, min(lim, 6))
    hints: List[str] = []
    for rec in _iter_records_reverse(store):
        roc = str(rec.get("outcome") or "").strip().lower()
        if roc not in _BAD_OUTCOMES:
            continue
        if str(rec.get("intent") or "") != want_int:
            continue
        if str(rec.get("module") or "") != want_mod:
            continue
        rfp = str(rec.get("fp") or "")
        if rfp != fp:
            continue
        mod = str(rec.get("module") or "").strip() or "?"
        det = str(rec.get("detail") or "").strip()
        ex = str(rec.get("assistant_excerpt") or "").strip()
        tail = ""
        if det:
            tail = f" — {det[:140]}"
        elif ex:
            tail = f" — {ex[:140]}"
        line = f"{roc} · {mod}{tail}"
        if line not in hints:
            hints.append(line)
        if len(hints) >= lim:
            break
    return hints


def should_attach_hint(
    *,
    decision: Any,
    predictive_hint: Optional[Dict[str, Any]],
    planned_module: str,
) -> bool:
    if not experience_enabled():
        return False
    if not (planned_module or "").strip() or normalize_module_key(planned_module) in {"__fallback__"}:
        return False
    try:
        conf = float((predictive_hint or {}).get("confidence"))
    except (TypeError, ValueError):
        conf = 0.0
    try:
        thr = float((os.getenv("EXPERIENCE_HINT_CONF_THRESHOLD") or "0.22").strip() or "0.22")
    except ValueError:
        thr = 0.22
    reason = str(getattr(decision, "reason", "") or "")
    if reason.startswith("chat_orchestrator_fallback"):
        return True
    if conf < thr:
        return True
    return False


def format_hint_block(hints: List[str]) -> str:
    if not hints:
        return ""
    lines = [
        "(Память успешных ответов: похожий запрос уже обрабатывался — учти идею и тон, не копируй дословно, если контекст другой.)",
    ]
    for i, h in enumerate(hints, start=1):
        lines.append(f"{i}. {h}")
    return "\n".join(lines)


def format_negative_hint_block(hints: List[str]) -> str:
    if not hints:
        return ""
    lines = [
        "(Память проблемных исходов: тот же тип запроса уже давал clarify/ошибку на этом маршруте — "
        "проверь формулировку, факты или выбери другой приём, не повторяй слепо прошлый ответ.)",
    ]
    for i, h in enumerate(hints, start=1):
        lines.append(f"{i}. {h}")
    return "\n".join(lines)


def build_hint_for_context(
    *,
    user_text: str,
    intent: str,
    module: str,
    decision: Any,
    predictive_hint: Optional[Dict[str, Any]],
) -> str:
    attach_pos = should_attach_hint(decision=decision, predictive_hint=predictive_hint, planned_module=module)
    neg = ""
    if experience_negative_hint_enabled():
        uncertain_only = _env_truthy("EXPERIENCE_NEGATIVE_HINT_UNCERTAIN_ONLY", False)
        if (not uncertain_only) or attach_pos:
            nh = find_negative_hints(user_text=user_text, intent=intent, module=module)
            neg = format_negative_hint_block(nh)
    pos = ""
    if attach_pos:
        pos = format_hint_block(find_hints(user_text=user_text, intent=intent, module=module))
    parts = [p for p in (neg, pos) if p]
    return "\n\n".join(parts)


def _count_sentences_ru(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    parts = [p for p in re.split(r"[.!?]+", t) if p.strip()]
    return len(parts)


def semantic_failure_reason(user_text: str, assistant_text: str) -> str:
    ut = str(user_text or "").strip()
    at = str(assistant_text or "").strip()
    if not ut or not at:
        return ""
    if _ONLY_NUMBER_REQ_RE.search(ut) and not (_INT_RE.match(at) or _NUM_RE.match(at)):
        return "format_only_number_violated"
    m = _N_SENT_RE.search(ut)
    if m:
        try:
            need = int(m.group(1))
        except ValueError:
            need = 0
        if need > 0:
            got = _count_sentences_ru(at)
            if got == 0 or got > need:
                return "format_sentence_limit_violated"
    # Шумовой ввод: длинная односимвольная «простыня» не должен превращаться в «точный числовой тест».
    if _NOISE_RUN_RE.search(ut) and _INT_RE.match(at):
        return "noise_misread_as_numeric_task"
    return ""


def _assistant_text_suggests_clarify(joined: str, *, user_text: str = "") -> bool:
    """
    Эвристика: модель задаёт уточняющий вопрос / просит конкретизировать.
    Вкл/выкл: TURN_OUTCOME_CLARIFY_HEURISTIC (по умолчанию true).
    """
    if not _env_truthy("TURN_OUTCOME_CLARIFY_HEURISTIC", True):
        return False
    ut = (user_text or "").strip()
    try:
        from core.prompt_routing import is_pure_chitchat_private

        if is_pure_chitchat_private(ut):
            return False
    except Exception as e:
        logger.debug("clarify heuristic chitchat skip: %s", e)
    t = (joined or "").strip()
    if len(t) < 14:
        return False
    low = t.lower()
    ends_q = t.rstrip().endswith("?")
    # Перевод: результат может быть вопросом (e.g. "How are you?"), но это не "уточнение".
    # Ловим частый ложный clarify по ends_q + how/what/which.
    if ut and re.search(r"(?i)\b(перевед(?:и|ите|и\s+на)|перевести|translate)\b", ut):
        # Исключение: если ассистент явно просит уточнить — это всё же clarify.
        if not _CLARIFY_HINT_RE.search(t):
            return False
    if _CLARIFY_HINT_RE.search(t) and (ends_q or len(t) < 360):
        return True
    ut_short = len(ut) < 56
    if ut_short and ends_q and len(t) < 1400 and _DISAMBIG_MENU_RE.search(t):
        return True
    if ut_short and ends_q and len(t) < 2800 and _FORK_CHOICE_RE.search(t):
        return True
    if ut_short and ends_q and len(t) < 520:
        if re.search(
            r"(?i)(какой|какая|какое|каког[ао]\s|что\s|где\b|когда\b|почему|зачем|укажи|выбер|"
            r"разобрать|имеется\s+ввиду|what\s+do\s+you\s+mean|which\s+(?:one|option)\b)",
            low,
        ):
            return True
    return False


_DIRECT_OK_FALLBACK_REASONS = frozenset(
    {
        "nl_reminder",
        "nl_weekly_schedule",
        "nl_cancel_reminder",
        "geo_nearby",
        "telegram_location",
        "weather_direct",
        "referential_math",
        "affirmative_search",
        "news_direct",
        "news_web_search",
        "news_item_direct",
        "article_thread_followup_nl",
        "direct_reply",
    }
)


def classify_turn_outcome(outputs: List[Any], *, user_text: str = "") -> str:
    """Грубая классификация исхода шага(ов) для записи в память."""
    for o in outputs or []:
        meta = getattr(o, "meta", None) or {}
        if not isinstance(meta, dict):
            continue
        if meta.get("module") == "__fallback__":
            r = str(meta.get("reason") or "")
            if r == "math_ambiguous":
                return "clarify"
            if r in _DIRECT_OK_FALLBACK_REASONS:
                return "ok"
            return "fallback"
        if meta.get("affirmative_search") or meta.get("module") == "news_reply":
            return "ok"
        if meta.get("facts_idle_ack"):
            return "ok"
        if meta.get("confirmation"):
            return "clarify"
    texts: List[str] = []
    for o in outputs or []:
        if getattr(o, "type", None) == "text":
            texts.append(str(getattr(o, "payload", "") or ""))
    joined = " ".join(texts).strip()
    if any("недоступен" in t for t in texts):
        return "error"
    try:
        from core.answer_quality import has_meta_tutor_text

        if joined and has_meta_tutor_text(joined):
            return "failure"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'experience_memory', e, exc_info=True)
    if joined:
        sem_fail = semantic_failure_reason(user_text, joined)
        if sem_fail:
            return "failure"
    if joined and _assistant_text_suggests_clarify(joined, user_text=user_text):
        try:
            from core.dialogue_feedback_signals import user_feedback_likely

            if user_feedback_likely(user_text):
                logger.debug(
                    "classify_turn_outcome: clarify suppressed (user_feedback user_chars=%s)",
                    len((user_text or "").strip()),
                )
                return "ok"
        except Exception as e:
            logger.debug('%s optional failed: %s', 'experience_memory', e, exc_info=True)
        logger.debug(
            "classify_turn_outcome: clarify heuristic (user_chars=%s reply_chars=%s)",
            len((user_text or "").strip()),
            len(joined),
        )
        return "clarify"
    if joined:
        return "ok"
    return "failure"
