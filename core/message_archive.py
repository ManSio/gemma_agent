"""
Отдельный от recent_messages журнал реплик для длинной памяти по времени/message_id.

Файл на сессию: data/behavior/message_archive/{user}__{group}.json
По умолчанию хранит до DIALOGUE_MESSAGE_ARCHIVE_MAX строк (роль user и assistant считаются отдельно).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE = os.path.join(os.getcwd(), "data")
_LOCK = threading.Lock()


def _session_key(user_id: str, group_id: Optional[str]) -> str:
    g = group_id or "dm"
    safe_u = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(user_id))[:128]
    safe_g = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(g))[:128]
    return f"{safe_u}__{safe_g}.json"


def _base_dir() -> str:
    try:
        from core.data_paths import message_archive_dir

        d = message_archive_dir()
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    except Exception:
        base = os.getenv("BEHAVIOR_DATA_DIR", _DEFAULT_BASE)
        d = os.path.join(base, "behavior", "message_archive")
        os.makedirs(d, exist_ok=True)
        return d


def _archive_enabled() -> bool:
    raw = os.getenv("DIALOGUE_MESSAGE_ARCHIVE_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _archive_max_items() -> int:
    try:
        return max(4, min(500, int(os.getenv("DIALOGUE_MESSAGE_ARCHIVE_MAX", "50"))))
    except ValueError:
        return 50


def _backfill_enabled() -> bool:
    raw = os.getenv("DIALOGUE_ARCHIVE_BACKFILL_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _backfill_last_n() -> int:
    try:
        return max(4, min(80, int(os.getenv("DIALOGUE_ARCHIVE_BACKFILL_LAST_N", "15"))))
    except ValueError:
        return 15


def _backfill_last_n_analysis() -> int:
    """Больше строк при явном запросе «разбери переписку / посмотри назад» и т.п."""
    try:
        return max(10, min(120, int(os.getenv("DIALOGUE_ARCHIVE_BACKFILL_LAST_N_ANALYSIS", "40"))))
    except ValueError:
        return 40


def _backfill_short_reply_chars() -> int:
    try:
        return max(0, min(2000, int(os.getenv("DIALOGUE_ARCHIVE_BACKFILL_SHORT_REPLY_CHARS", "120"))))
    except ValueError:
        return 120


def _rows_for_dialogue(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip() or "?"
        out.append({"role": role, "text": str(m.get("text") or "")[:4000]})
    return out


def _prompt_tail_max_messages() -> int:
    try:
        n = int(os.getenv("MESSAGE_ARCHIVE_PROMPT_TAIL", "8"))
        return max(4, min(40, n - (n % 2)))
    except ValueError:
        return 8


def items_for_prompt(user_id: Optional[str], group_id: Optional[str]) -> List[Dict[str, Any]]:
    """Хвост архива для промпта: только выровненные пары user/assistant."""
    items = load_message_archive_items(str(user_id or ""), group_id)
    if not items:
        return []
    try:
        from core.context_compression import trim_dialogue_messages_paired

        cap = _prompt_tail_max_messages()
        return _rows_for_dialogue(trim_dialogue_messages_paired(items, cap))
    except Exception:
        return _rows_for_dialogue(items[-_prompt_tail_max_messages() :])


def should_backfill_dialogue_from_archive(
    *,
    user_text: str,
    recent_dialogue: Any,
    input_meta: Optional[Dict[str, Any]],
) -> bool:
    """
    Расширить recent_dialogue из архива при «тонком» контексте: пересылка, продолжение темы,
    очень короткая reply-цепочка в метаданных, мало строк в скользящем окне.
    """
    if not _backfill_enabled() or not _archive_enabled():
        return False
    try:
        from core.prompt_routing import text_looks_dialog_followup_cue, user_requests_dialogue_analysis
    except Exception as e:
        logger.debug("backfill import prompt_routing: %s", e)
        return False
    if user_requests_dialogue_analysis(user_text or ""):
        return True
    try:
        from core.meta_intent_probe import dialogue_review_from_meta

        if dialogue_review_from_meta(input_meta if isinstance(input_meta, dict) else None):
            return True
    except Exception as e:
        logger.debug("backfill meta_intent: %s", e)
    meta = input_meta if isinstance(input_meta, dict) else {}
    if meta.get("telegram_has_forward"):
        return True
    if text_looks_dialog_followup_cue(user_text or ""):
        return True
    trc = str(meta.get("telegram_reply_context") or "").strip()
    lim = _backfill_short_reply_chars()
    if trc and lim > 0 and len(trc) < lim:
        return True
    rd = recent_dialogue if isinstance(recent_dialogue, list) else []
    ut = (user_text or "").strip()
    # Короткие реплики («почему», «покажи») — не подменять recent архивом:
    # иначе модель отвечает на старый вопрос из испорченного хвоста.
    if len(rd) < 4 and len(ut) < 48:
        if len(ut.split()) <= 2 and not text_looks_dialog_followup_cue(ut):
            return False
        return True
    return False


def maybe_backfill_context_recent_dialogue(
    ctx: Dict[str, Any],
    *,
    user_id: Optional[str],
    group_id: Optional[str],
    user_text: str,
    input_meta: Optional[Dict[str, Any]],
) -> None:
    """Подменяет ctx['recent_dialogue'] хвостом архива (последние N строк), если сработали эвристики."""
    if not user_id or not isinstance(ctx, dict):
        return
    rd0 = ctx.get("recent_dialogue")
    if not should_backfill_dialogue_from_archive(
        user_text=user_text,
        recent_dialogue=rd0,
        input_meta=input_meta,
    ):
        return
    items = load_message_archive_items(str(user_id), group_id)
    if not items:
        return
    n = _backfill_last_n()
    try:
        from core.prompt_routing import user_requests_dialogue_analysis

        try:
            from core.meta_intent_probe import dialogue_review_from_meta

            _dr = user_requests_dialogue_analysis(user_text or "") or dialogue_review_from_meta(
                input_meta if isinstance(input_meta, dict) else None
            )
        except Exception:
            _dr = user_requests_dialogue_analysis(user_text or "")
        if _dr:
            n = max(n, _backfill_last_n_analysis())
    except Exception as e:
        logger.debug('%s optional failed: %s', 'message_archive', e, exc_info=True)
    try:
        from core.context_compression import trim_dialogue_messages_paired

        cap = max(4, n - (n % 2))
        tail = trim_dialogue_messages_paired(items, cap)
    except Exception:
        tail = items[-n:]
    ctx["recent_dialogue"] = _rows_for_dialogue(tail)


def _path(user_id: str, group_id: Optional[str]) -> str:
    return os.path.join(_base_dir(), _session_key(user_id, group_id))


def _sanitize_row(m: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"role": str(m.get("role") or "")[:16], "text": str(m.get("text") or "")[:4000]}
    if m.get("telegram_ts") is not None:
        try:
            out["telegram_ts"] = int(m["telegram_ts"])
        except (TypeError, ValueError):
            pass
    if m.get("telegram_message_id") is not None:
        try:
            out["telegram_message_id"] = int(m["telegram_message_id"])
        except (TypeError, ValueError):
            pass
    return out


def load_message_archive_items(user_id: Optional[str], group_id: Optional[str]) -> List[Dict[str, Any]]:
    if not user_id or not _archive_enabled():
        return []
    path = _path(str(user_id), group_id)
    with _LOCK:
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return []
            items = raw.get("items")
            if not isinstance(items, list):
                return []
            return [x for x in items if isinstance(x, dict)]
        except Exception as e:
            logger.debug("message_archive load failed: %s", e)
            return []


def append_turn_to_message_archive(
    user_id: str,
    group_id: Optional[str],
    user_row: Dict[str, Any],
    assistant_text: str,
) -> None:
    if not user_id or not _archive_enabled():
        return
    path = _path(str(user_id), group_id)
    max_items = _archive_max_items()
    u = _sanitize_row(user_row if isinstance(user_row, dict) else {"role": "user", "text": ""})
    if not u.get("role"):
        u["role"] = "user"
    a = {"role": "assistant", "text": str(assistant_text or "")[:4000]}
    with _LOCK:
        items: List[Dict[str, Any]] = []
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    old = raw.get("items")
                    if isinstance(old, list):
                        items = [x for x in old if isinstance(x, dict)]
            except Exception as e:
                logger.debug("message_archive read before append: %s", e)
                items = []
        items.append(u)
        items.append(a)
        if len(items) > max_items:
            try:
                from core.context_compression import trim_dialogue_messages_paired

                cap = max_items - (max_items % 2)
                items = trim_dialogue_messages_paired(items, cap)
            except Exception:
                items = items[-max_items:]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "items": items}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("message_archive write failed: %s", e)
