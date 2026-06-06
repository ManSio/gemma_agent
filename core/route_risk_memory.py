"""
Профили «риска» маршрута: повторяющиеся fallback/clarify/error по похожим запросам → стратегия в hint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence

from core.experience_memory import fingerprint, normalize_module_key, normalize_user_text
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def route_risk_enabled() -> bool:
    """Писать stumble в route_risk.jsonl."""
    return effective_bool("ROUTE_RISK_MEMORY_ENABLED", default=True)


def route_risk_hint_enabled() -> bool:
    """Подмешивать route_risk_hint в контекст брейна (читать jsonl независимо от записи)."""
    return effective_bool("ROUTE_RISK_HINT_ENABLED", default=True)


def record_clarify_as_stumble() -> bool:
    """По умолчанию clarify не пишем в route_risk (шум ~190/218)."""
    return effective_bool("ROUTE_RISK_RECORD_CLARIFY", default=False)


def should_record_stumble(
    *,
    outcome: str,
    detail: str = "",
    user_feedback_negative: bool = False,
) -> bool:
    """
    Реальный stumble: failure/error/fallback, опционально clarify, или явная жалоба в реплике.
    """
    if user_feedback_negative:
        return True
    o = (outcome or "").strip().lower()
    if o in ("failure", "error", "fallback"):
        return True
    if o == "clarify":
        d = (detail or "").strip().lower()
        if "math_ambiguous" in d:
            return True
        if not record_clarify_as_stumble():
            return False
        return "route" in d or "fallback" in d
    return False


def default_path() -> str:
    p = (os.getenv("GEMMA_ROUTE_RISK_PATH") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "route_risk.jsonl")


def _trim_tail(path: str, max_lines: int) -> None:
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
        logger.debug("route_risk trim: %s", e)


def loose_bucket_fingerprint(text: str, *, max_norm_chars: int = 96) -> str:
    """Отпечаток по началу нормализованного текста — для похожих формулировок (опционально в hint)."""
    norm = normalize_user_text(text)
    if not norm:
        return ""
    snippet = norm[: max(8, int(max_norm_chars))]
    return hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]


def outcome_severity(outcome: str) -> int:
    """Грубая важность для отчётов и силы hint: error/failure выше clarify."""
    o = (outcome or "").strip().lower()
    if o in ("error", "failure"):
        return 3
    if o == "fallback":
        return 2
    if o == "clarify":
        return 1
    return 1


def stumble_from_turn_quality_loop(detail: str) -> bool:
    """True если строка route_risk записана turn_quality_loop (обучение), не сбой маршрутизации."""
    d = (detail or "").strip().lower()
    return d.startswith("quality_loop:") or "quality_loop:" in d


def classify_error_type(*, outcome: str, detail: str, module: str) -> str:
    """
    Минимальная таксономия для CDC/отчётов:
    tool | model | router | policy | user_input | unknown.
    """
    o = (outcome or "").strip().lower()
    d = (detail or "").strip().lower()
    m = (module or "").strip().lower()
    if "math_ambiguous" in d or o == "clarify":
        return "user_input"
    if "__fallback__" in m or "fallback" in d or "route" in d or "planner" in d:
        return "router"
    if any(tok in d for tok in ("timeout", "429", "rate", "tool", "api", "network", "http")):
        return "tool"
    if any(tok in d for tok in ("hallucin", "template", "операцион", "diag", "policy")):
        return "policy"
    if o in ("error", "failure"):
        return "model"
    return "unknown"


def _record_within_ttl(rec: Dict[str, Any], ttl_sec: int) -> bool:
    if ttl_sec <= 0:
        return True
    ts_raw = rec.get("ts")
    if ts_raw is None or ts_raw == "":
        return True
    try:
        s = str(ts_raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age <= float(ttl_sec)
    except Exception:
        return True


def _record_matches_query(
    rec: Dict[str, Any],
    *,
    fp: str,
    bucket_fp: str,
    want_int: str,
    cluster_match: bool,
) -> bool:
    if str(rec.get("intent") or "").strip() != want_int:
        return False
    rf = str(rec.get("fp") or "")
    if rf == fp:
        return True
    if cluster_match and bucket_fp:
        bf_rec = str(rec.get("bucket_fp") or "")
        if bf_rec and bf_rec == bucket_fp:
            return True
    return False


def stumble_detail_from_outputs(outputs: Sequence[Any]) -> str:
    for o in outputs or []:
        meta = getattr(o, "meta", None) or {}
        if isinstance(meta, dict) and meta.get("module") == "__fallback__":
            return str(meta.get("reason") or "")
    return ""


def _last_stumble_is_same(store: str, fp: str, intent: str, outcome: str) -> bool:
    """Проверить, что последняя запись stumble для этого fp+intent уже имеет такой же outcome."""
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, FileNotFoundError):
        return False
    want_int = (intent or "").strip() or "unknown"
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
        if str(rec.get("intent") or "") != want_int:
            continue
        return str(rec.get("outcome") or "") == outcome
    return False


def record_stumble(
    *,
    user_text: str,
    intent: str,
    module: str,
    outcome: str,
    detail: str = "",
    error_type: str = "",
    path: Optional[str] = None,
    skill_name: str = "",
) -> None:
    if not route_risk_enabled():
        return
    fp = fingerprint(user_text)
    if not fp:
        return
    if not should_record_stumble(outcome=outcome, detail=detail):
        return
    store = path or default_path()
    try:
        os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    except OSError:
        pass
    rec = {
        "ts": _now_iso(),
        "fp": fp,
        "bucket_fp": loose_bucket_fingerprint(user_text),
        "intent": (intent or "").strip() or "unknown",
        "skill": (skill_name or "").strip() or None,
        "module": normalize_module_key(module),
        "outcome": outcome,
        "detail": (detail or "")[:120],
        "severity": outcome_severity(outcome),
        "error_type": (error_type or classify_error_type(outcome=outcome, detail=detail, module=module))[:24],
    }
    # Дедупликация: если последний stumble по этому fp+intent уже clarify — не накапливаем дубли
    if outcome == "clarify" and _last_stumble_is_same(store, fp, intent, outcome):
        return
    try:
        with open(store, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("route_risk append: %s", e)
        return
    try:
        max_lines = int((os.getenv("ROUTE_RISK_MAX_LINES") or "8000").strip() or "8000")
        if max_lines > 0 and os.path.isfile(store) and os.path.getsize(store) > 1_500_000:
            _trim_tail(store, max_lines)
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


def _strategy_line(outcome: str, detail: str) -> str:
    d = (detail or "").lower()
    if outcome == "clarify" or "math_ambiguous" in d:
        return (
            "Ранее по похожим запросам требовалось уточнение: сразу предложи явный выбор "
            "(например, «нужен расчёт» vs «просто текст»), не отвечай общими фразами."
        )
    if outcome == "error":
        return (
            "Ранее ответ прерывался ошибкой модуля/API: коротко объясни и предложи альтернативу или /help."
        )
    if outcome == "failure":
        return "Ранее ответ не сформировался: задай одно уточняющее предложение по сути запроса."
    return (
        "Ранее срабатывал запасной маршрут: лучше задать короткий уточняющий вопрос, чем общее «не понял»."
    )


def _hint_intro_for_level(level: str) -> str:
    if level == "soft":
        return (
            "(Подсказка маршрута: недавно были проблемы с похожими запросами — будь точнее, без шаблонных отписок.)\n"
        )
    if level == "strong":
        return (
            "(Важно — память маршрута: повторяющиеся сбои по этому запросу; строго следуй стратегии ниже, "
            "не уходи в общие фразы и не повторяй прошлую ошибку.)\n"
        )
    return (
        "(Память маршрута: этот же запрос недавно давал сбой/уточнение — действуй по стратегии ниже.)\n"
    )


def _hint_level_from_matches(matches: List[Dict[str, Any]]) -> str:
    if not matches:
        return "firm"
    outs = [str(m.get("outcome") or "").strip().lower() for m in matches]
    if any(o in ("error", "failure") for o in outs):
        return "strong"
    if len(matches) >= 3:
        return "strong"
    try:
        sev_sum = sum(int(m.get("severity") or outcome_severity(str(m.get("outcome")))) for m in matches)
    except (TypeError, ValueError):
        sev_sum = len(matches) * 2
    if sev_sum >= 5:
        return "strong"
    if len(matches) <= 2 and outs and all(o == "clarify" for o in outs):
        return "soft"
    return "firm"


def build_route_risk_hint(*, user_text: str, intent: str, path: Optional[str] = None) -> str:
    if not route_risk_hint_enabled():
        return ""
    fp = fingerprint(user_text)
    if not fp:
        return ""
    bucket_fp = loose_bucket_fingerprint(user_text)
    want_int = (intent or "").strip() or "unknown"
    cluster_match = effective_bool("ROUTE_RISK_CLUSTER_MATCH", default=False)
    try:
        ttl_sec = int((os.getenv("ROUTE_RISK_HINT_TTL_SEC") or "604800").strip() or "604800")
    except ValueError:
        ttl_sec = 604800
    try:
        need = max(2, int((os.getenv("ROUTE_RISK_MIN_STUMBLES") or "2").strip() or "2"))
    except ValueError:
        need = 2
    try:
        window = max(20, int((os.getenv("ROUTE_RISK_LOOKBACK_LINES") or "120").strip() or "120"))
    except ValueError:
        window = 120
    store = path or default_path()
    matches: List[Dict[str, Any]] = []
    n = 0
    for rec in _iter_reverse(store):
        n += 1
        if n > window:
            break
        if not isinstance(rec, dict):
            continue
        if not _record_within_ttl(rec, ttl_sec):
            continue
        if not _record_matches_query(
            rec, fp=fp, bucket_fp=bucket_fp, want_int=want_int, cluster_match=cluster_match
        ):
            continue
        matches.append(rec)
        if len(matches) >= need:
            break
    if len(matches) < need:
        return ""
    last = matches[0]
    out = str(last.get("outcome") or "fallback")
    det = str(last.get("detail") or "")
    strat = _strategy_line(out, det)
    level = _hint_level_from_matches(matches)
    intro = _hint_intro_for_level(level)
    return intro + strat
