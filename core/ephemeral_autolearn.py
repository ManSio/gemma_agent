"""
Автонакопление временных латок без /remember_patch:
- доверенный пользователь (админ или EPHEMERAL_AUTOLEARN_TRUST_USER_IDS): повтор похожих правок → add_lesson;
- остальные: предложения в очередь → одобрение админом (/approve_suggested_patch)
  или авто-промоут при EPHEMERAL_PENDING_AUTO_PROMOTE_USERS разных user_id на одном fingerprint.

EPHEMERAL_AUTOLEARN=1|0 — включено (по умолчанию 1).
EPHEMERAL_AUTOLEARN_STRIKES_TRUSTED=2 — вес «жалобы» для доверенных до промоута.
EPHEMERAL_AUTOLEARN_STRIKES_UNTRUSTED=999 — только очередь (не промоутить автоматически).
EPHEMERAL_AUTOLEARN_MAX_PROMOTIONS_PER_DAY=30
EPHEMERAL_PENDING_AUTO_PROMOTE_USERS=0 — если >=2, при стольких разных пользователях на одной заявке → add_lesson (учитывается дневной лимит промоутов).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.ephemeral_lessons import add_lesson, lessons_path
from core.intent_heuristics import user_asked_disable_calculator_router

logger = logging.getLogger(__name__)

_lock = threading.RLock()

_REMEMBER = re.compile(
    r"(?i)(запомни|запоминай|зафиксируй|не\s+забывай|запиши\s+себе)[:;,.!\s]+(.{8,480})"
)
_COMPLAINT = re.compile(
    r"(?i)(ты\s+опять|снова\s+так|не\s+так|не\s+то|перестань|хватит|бесит|"
    r"не\s+надо\s+так|исправься|учись\s+на|не\s+учишься|"
    r"ложн\w*\s+срабатыван|ошибаешься|не\s+работает\s+так)",
)


def _repo_root() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path(__file__).resolve().parent.parent


def _runtime_dir() -> Path:
    raw = (os.getenv("RESILIENCE_RUNTIME_DIR") or "data/runtime").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _repo_root() / p
    return p.resolve()


def pending_path() -> Path:
    env = (os.getenv("EPHEMERAL_PENDING_PATH") or "").strip()
    if env:
        pp = Path(env)
        return pp.resolve() if pp.is_absolute() else (_repo_root() / pp).resolve()
    return _runtime_dir() / "ephemeral_pending.json"


def _promo_state_path() -> Path:
    return _runtime_dir() / "ephemeral_autolearn_promo_state.json"


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _trusted_ids() -> set:
    raw = (os.getenv("EPHEMERAL_AUTOLEARN_TRUST_USER_IDS") or "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.replace(";", ",").split(",") if x.strip()}


def is_trusted_autolearn_user(user_id: str, *, telegram_is_admin: bool) -> bool:
    if telegram_is_admin:
        return True
    uid = str(user_id or "").strip()
    return uid in _trusted_ids()


def _bot_behavior_context(text: str) -> bool:
    t = (text or "").lower()
    hints = (
        "калькулятор",
        "/calc",
        "бот ",
        "ссыл",
        "t.me",
        "telegram",
        "инструмент",
        "selfprogramming",
        "разговор",
        "диалог",
        "переписк",
        "анализ",
        "латк",
        "исправ",
        "маршрут",
        "intent",
        "не предлагай",
        "не суй",
        "приглаш",
        "инвайт",
        "утечк",
        "рассужден",
    )
    return any(h in t for h in hints)


def _personal_fact_noise(text: str) -> bool:
    t = (text or "").lower()
    if re.search(
        r"(?i)(меня\s+зовут|мне\s+\d+\s+лет|я\s+из\s+|люблю\s+|ненавижу\s+|др\s+|день\s+рожд)",
        t,
    ):
        return _bot_behavior_context(text) is False
    return False


def _guess_trigger_and_force(instruction: str, user_text: str) -> Tuple[str, bool]:
    blob = f"{instruction} {user_text}".lower()
    fg = user_asked_disable_calculator_router(user_text) or user_asked_disable_calculator_router(
        instruction
    )
    if "t.me/" in blob or "telegram.me/" in blob or "приглаш" in blob or "инвайт" in blob:
        if fg or "/calc" in blob or "калькулятор" in blob:
            return "t.me/", True
        return "t.me/", fg
    if fg:
        return "+", True
    if re.search(r"(?i)https?://", user_text or ""):
        m = re.search(r"(?i)(https?://[^\s]+)", user_text or "")
        if m:
            u = m.group(1)[:80]
            return u, fg
    words = re.findall(r"[\w/+.-]{4,40}", (user_text or "")[:200])
    if words:
        return words[0][:36], fg
    tail = (instruction or "").strip()[:40]
    return tail if tail else "user_feedback", fg


def extract_lesson_draft(user_text: str, assistant_text: str) -> Optional[Dict[str, Any]]:
    """
    Черновик латки или None. weight: 2 = сильный сигнал (запомни / явная жалоба на маршрутизацию).
    """
    raw = (user_text or "").strip()
    if len(raw) < 10 or _personal_fact_noise(raw):
        return None
    if not _bot_behavior_context(raw) and not _COMPLAINT.search(raw):
        return None

    m = _REMEMBER.search(raw)
    if m:
        instruction = m.group(2).strip()
        if len(instruction) < 8:
            return None
        trig, fg = _guess_trigger_and_force(instruction, raw)
        fp = _fingerprint(trig, instruction, fg)
        return {
            "fingerprint": fp,
            "trigger": trig,
            "instruction": instruction[:900],
            "force_general_when_math_probe": fg,
            "weight": 2,
            "source": "remember_phrase",
        }

    if user_asked_disable_calculator_router(raw) or (
        _COMPLAINT.search(raw) and _bot_behavior_context(raw)
    ):
        instruction = raw[:900]
        trig, fg = _guess_trigger_and_force(instruction, raw)
        fp = _fingerprint(trig, instruction, fg)
        w = 2 if user_asked_disable_calculator_router(raw) else 1
        return {
            "fingerprint": fp,
            "trigger": trig,
            "instruction": instruction,
            "force_general_when_math_probe": fg,
            "weight": w,
            "source": "complaint",
        }

    if _COMPLAINT.search(raw) and len(raw) >= 24:
        instruction = raw[:900]
        trig, fg = _guess_trigger_and_force(instruction, raw)
        return {
            "fingerprint": _fingerprint(trig, instruction, fg),
            "trigger": trig,
            "instruction": instruction,
            "force_general_when_math_probe": fg,
            "weight": 1,
            "source": "complaint_loose",
        }

    return None


def _fingerprint(trigger: str, instruction: str, fg: bool) -> str:
    norm = " ".join(f"{trigger}|{instruction}|{fg}".lower().split())
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _strikes_threshold(*, trusted: bool) -> int:
    if trusted:
        try:
            return max(1, int(os.getenv("EPHEMERAL_AUTOLEARN_STRIKES_TRUSTED", "2")))
        except ValueError:
            return 2
    try:
        v = int(os.getenv("EPHEMERAL_AUTOLEARN_STRIKES_UNTRUSTED", "999"))
    except ValueError:
        v = 999
    return max(1, v)


def _max_promo_day() -> int:
    try:
        return max(1, int(os.getenv("EPHEMERAL_AUTOLEARN_MAX_PROMOTIONS_PER_DAY", "30")))
    except ValueError:
        return 30


def _auto_promote_distinct_users() -> int:
    """0 = выключено; иначе минимум разных Telegram user_id на одной pending-заявке."""
    try:
        return max(0, int(os.getenv("EPHEMERAL_PENDING_AUTO_PROMOTE_USERS", "0")))
    except ValueError:
        return 0


def _add_lesson_from_pending_item(it: Dict[str, Any], *, source: str) -> Optional[Dict[str, Any]]:
    pid = str(it.get("id") or "")
    try:
        return add_lesson(
            str(it.get("trigger") or "user_feedback"),
            str(it.get("instruction") or "").strip(),
            force_general_when_math_probe=bool(it.get("force_general_when_math_probe")),
            meta={
                "source": source,
                "from_user_id": it.get("from_user_id"),
                "pending_id": pid,
                "supporter_user_ids": it.get("supporter_user_ids"),
            },
        )
    except ValueError as e:
        logger.info("pending → lesson failed: %s", e)
        return None


def _merge_supporter_uids(it: Dict[str, Any], from_user_id: str) -> List[str]:
    uid = str(from_user_id or "").strip()
    uids = [str(x) for x in (it.get("supporter_user_ids") or []) if str(x).strip()]
    if not uids:
        seed = str(it.get("from_user_id") or "").strip()
        if seed:
            uids = [seed]
    if uid and uid not in uids:
        uids.append(uid)
    return uids


def _consume_promo_slot() -> bool:
    """True если слот есть и зарезервирован."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    path = _promo_state_path()
    with _lock:
        data: Dict[str, Any] = {"day": day, "count": 0}
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and raw.get("day") == day:
                    data["count"] = int(raw.get("count") or 0)
            except (OSError, json.JSONDecodeError):
                pass
        lim = _max_promo_day()
        if data["count"] >= lim:
            return False
        data["count"] = data["count"] + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        return True


def _pending_load() -> Dict[str, Any]:
    path = pending_path()
    if not path.is_file():
        return {"version": 1, "items": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"version": 1, "items": []}
        if not isinstance(d.get("items"), list):
            d["items"] = []
        return d
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("ephemeral pending load failed: %s", e)
        return {"version": 1, "items": []}


def _pending_save(doc: Dict[str, Any]) -> None:
    path = pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def pending_append(
    draft: Dict[str, Any],
    *,
    from_user_id: str,
    group_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Добавить или обновить очередь.
    Возвращает новую pending-строку; при обновлении — None, либо {"auto_promoted": True, "lesson": {...}} если сработал порог разных пользователей.
    """
    fp = draft.get("fingerprint")
    if not fp:
        return None
    with _lock:
        doc = _pending_load()
        items: List[Dict[str, Any]] = [x for x in doc.get("items") or [] if isinstance(x, dict)]
        auto_n = _auto_promote_distinct_users()
        for it in items:
            if it.get("fingerprint") == fp and it.get("status") == "pending":
                uids = _merge_supporter_uids(it, from_user_id)
                it["supporter_user_ids"] = uids
                it["supporters"] = len(uids)
                it["last_user_id"] = str(from_user_id)
                it["updated_ts"] = time.time()
                it["last_instruction"] = draft.get("instruction")
                extra: Optional[Dict[str, Any]] = None
                if auto_n > 0 and len(uids) >= auto_n:
                    if _consume_promo_slot():
                        le = _add_lesson_from_pending_item(it, source="pending_auto_distinct_users")
                        if le:
                            it["status"] = "approved"
                            it["lesson_id"] = le.get("id")
                            it["updated_ts"] = time.time()
                            logger.info(
                                "ephemeral pending auto-promoted id=%s lesson=%s distinct_users=%s",
                                it.get("id"),
                                le.get("id"),
                                len(uids),
                            )
                            extra = {"auto_promoted": True, "lesson": le}
                    else:
                        logger.warning("ephemeral pending auto-promote skipped: daily cap")
                doc["items"] = items
                _pending_save(doc)
                return extra
        uid0 = str(from_user_id).strip()
        row = {
            "id": secrets.token_hex(5),
            "fingerprint": fp,
            "from_user_id": str(from_user_id),
            "group_id": group_id,
            "trigger": draft.get("trigger"),
            "instruction": draft.get("instruction"),
            "force_general_when_math_probe": bool(draft.get("force_general_when_math_probe")),
            "source": draft.get("source"),
            "supporter_user_ids": [uid0] if uid0 else [],
            "supporters": 1 if uid0 else 0,
            "status": "pending",
            "created_ts": time.time(),
            "updated_ts": time.time(),
        }
        items.append(row)
        doc["items"] = items[-200:]
        extra_new: Optional[Dict[str, Any]] = None
        uids_new = row.get("supporter_user_ids") or []
        if auto_n > 0 and len(uids_new) >= auto_n:
            if _consume_promo_slot():
                le = _add_lesson_from_pending_item(row, source="pending_auto_distinct_users")
                if le:
                    row["status"] = "approved"
                    row["lesson_id"] = le.get("id")
                    row["updated_ts"] = time.time()
                    logger.info(
                        "ephemeral pending auto-promoted (new) id=%s lesson=%s distinct_users=%s",
                        row.get("id"),
                        le.get("id"),
                        len(uids_new),
                    )
                    extra_new = {"auto_promoted": True, "lesson": le}
            else:
                logger.warning("ephemeral pending auto-promote skipped: daily cap")
        _pending_save(doc)
        return extra_new if extra_new else row


def pending_list(*, status: str = "pending") -> List[Dict[str, Any]]:
    doc = _pending_load()
    out = [x for x in doc.get("items") or [] if isinstance(x, dict) and x.get("status") == status]
    out.sort(key=lambda x: float(x.get("created_ts") or 0.0), reverse=True)
    return out


def pending_approve(item_id: str) -> Optional[Dict[str, Any]]:
    lid = (item_id or "").strip()
    if not lid:
        return None
    with _lock:
        doc = _pending_load()
        items = [x for x in doc.get("items") or [] if isinstance(x, dict)]
        target = None
        for it in items:
            if it.get("id") == lid and it.get("status") == "pending":
                target = it
                break
        if not target:
            return None
        le = _add_lesson_from_pending_item(target, source="pending_approve")
        if not le:
            return None
        target["status"] = "approved"
        target["lesson_id"] = le.get("id")
        target["updated_ts"] = time.time()
        doc["items"] = items
        _pending_save(doc)
        return le


def pending_clear_all_pending() -> int:
    """Убрать все заявки со статусом pending (очередь /pending_suggested_patch)."""
    with _lock:
        doc = _pending_load()
        items = [x for x in doc.get("items") or [] if isinstance(x, dict)]
        kept: List[Dict[str, Any]] = []
        n = 0
        for it in items:
            if it.get("status") == "pending":
                n += 1
                continue
            kept.append(it)
        doc["items"] = kept
        if n:
            _pending_save(doc)
        return n


def pending_dismiss(item_id: str) -> bool:
    lid = (item_id or "").strip()
    if not lid:
        return False
    with _lock:
        doc = _pending_load()
        items = [x for x in doc.get("items") or [] if isinstance(x, dict)]
        ok = False
        for it in items:
            if it.get("id") == lid and it.get("status") == "pending":
                it["status"] = "dismissed"
                it["updated_ts"] = time.time()
                ok = True
                break
        if ok:
            doc["items"] = items
            _pending_save(doc)
        return ok


def _prune_buckets(buckets: Dict[str, Any], *, max_age_sec: float = 86400 * 14) -> None:
    now = time.time()
    dead = []
    for k, v in buckets.items():
        if not isinstance(v, dict):
            dead.append(k)
            continue
        if now - float(v.get("last_ts") or 0.0) > max_age_sec:
            dead.append(k)
    for k in dead:
        buckets.pop(k, None)
    if len(buckets) > 40:
        items = sorted(buckets.items(), key=lambda kv: float(kv[1].get("last_ts") or 0.0))
        for k, _ in items[: len(buckets) - 40]:
            buckets.pop(k, None)


def process_turn_for_autolearn(
    record: Dict[str, Any],
    user_text: str,
    assistant_text: str,
    *,
    user_id: str,
    group_id: Optional[str],
    telegram_is_admin: bool,
) -> Optional[Dict[str, Any]]:
    """
    Обновляет record['ephemeral_autolearn'], при промоуте — add_lesson.
    Возвращает краткий dict для логов: {promoted, lesson_id, pending_id, note}.
    """
    if not _env_bool("EPHEMERAL_AUTOLEARN", True):
        return None
    draft = extract_lesson_draft(user_text, assistant_text or "")
    if not draft:
        return None
    trusted = is_trusted_autolearn_user(user_id, telegram_is_admin=telegram_is_admin)
    threshold = _strikes_threshold(trusted=trusted)

    al = dict(record.get("ephemeral_autolearn") or {})
    buckets: Dict[str, Any] = dict(al.get("buckets") or {})
    fp = draft["fingerprint"]
    w = int(draft.get("weight") or 1)
    now = time.time()
    b = dict(buckets.get(fp) or {})
    b["weight_sum"] = int(b.get("weight_sum") or 0) + w
    b["last_ts"] = now
    b["trigger"] = draft.get("trigger")
    b["instruction"] = draft.get("instruction")
    b["force_general_when_math_probe"] = draft.get("force_general_when_math_probe")
    buckets[fp] = b
    _prune_buckets(buckets)
    al["buckets"] = buckets
    record["ephemeral_autolearn"] = al

    if int(b["weight_sum"]) < threshold:
        if not trusted:
            row = pending_append(
                draft,
                from_user_id=user_id,
                group_id=group_id,
            )
            if isinstance(row, dict) and row.get("auto_promoted"):
                le = row.get("lesson") or {}
                return {
                    "promoted": True,
                    "lesson_id": le.get("id"),
                    "note": "pending_auto_distinct_users",
                }
            pid = row.get("id") if isinstance(row, dict) and row.get("id") else None
            return {
                "promoted": False,
                "pending_id": pid,
                "note": "queued_non_trusted",
            }
        return {"promoted": False, "note": "accumulating", "weight": b["weight_sum"], "need": threshold}

    if not trusted:
        pending_append(draft, from_user_id=user_id, group_id=group_id)
        b["weight_sum"] = 0
        buckets[fp] = b
        record["ephemeral_autolearn"] = al
        return {"promoted": False, "note": "threshold_only_for_trusted"}

    if not _consume_promo_slot():
        logger.warning("ephemeral_autolearn: daily promotion cap reached")
        return {"promoted": False, "note": "promo_cap"}

    try:
        le = add_lesson(
            str(draft.get("trigger") or "user_feedback"),
            str(draft.get("instruction") or "").strip(),
            force_general_when_math_probe=bool(draft.get("force_general_when_math_probe")),
            meta={
                "source": "autolearn",
                "user_id": str(user_id),
                "fingerprint": fp,
            },
        )
    except ValueError as e:
        logger.info("autolearn add_lesson: %s", e)
        return {"promoted": False, "note": str(e)}
    b["weight_sum"] = 0
    buckets[fp] = b
    record["ephemeral_autolearn"] = al
    logger.info(
        "ephemeral_autolearn promoted lesson=%s user=%s fp=%s",
        le.get("id"),
        user_id,
        fp[:12],
    )
    return {"promoted": True, "lesson_id": le.get("id")}


def snapshot_for_operator() -> Dict[str, Any]:
    p = pending_path()
    doc = _pending_load()
    pend = [x for x in doc.get("items") or [] if isinstance(x, dict) and x.get("status") == "pending"]
    return {
        "pending_path": str(p),
        "pending_count": len(pend),
        "pending_auto_promote_distinct_users": _auto_promote_distinct_users(),
        "lessons_path": str(lessons_path()),
        "trusted_env": "EPHEMERAL_AUTOLEARN_TRUST_USER_IDS",
    }
