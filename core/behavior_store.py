"""
Persistent dialogue / group behavior state (short-term memory, topics, style anchors).

JSON files under data/behavior — no routing or output schema changes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from core.regex_safe import strip_trailing_sentence_punct

from core.intent_heuristics import merge_routing_prefs_from_turn
from core.group_chat_policy import load_group_chat_policy
from core.context_compression import (
    compress_dialogue_summary,
    compress_recent_dialogue,
    normalize_dialogue_message_rows,
    trim_dialogue_messages_paired,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE = os.path.join(os.getcwd(), "data")


def _get_recent_messages_limit() -> int:
    """Get the limit for recent messages from environment or default."""
    try:
        return max(4, min(50, int(os.getenv("BRAIN_CONTEXT_LOAD_RECENT_LIMIT", "10"))))
    except (TypeError, ValueError):
        return 10


class DialogueCompactPending(TypedDict):
    """Если задано — оркестратор может асинхронно заменить snippet-сводку на LLM-абзац."""

    user_id: str
    group_id: Optional[str]
    summary_before: str
    summary_after_snippet: str
    overflow_messages: List[Dict[str, Any]]
    max_summary: int


def _dialogue_compact_llm_enabled() -> bool:
    raw = os.getenv("DIALOGUE_COMPACT_LLM", "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _overflow_summary_enabled() -> bool:
    raw = os.getenv("DIALOGUE_SUMMARY_ON_OVERFLOW")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _session_key(user_id: str, group_id: Optional[str]) -> str:
    g = group_id or "dm"
    safe_u = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(user_id))[:128]
    safe_g = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(g))[:128]
    return f"{safe_u}__{safe_g}.json"


_SHORT_TOPIC_FOLLOWUPS = frozenset(
    {
        "почему",
        "зачем",
        "как",
        "что",
        "когда",
        "где",
        "откуда",
        "куда",
        "смазка",
        "дальше",
        "ещё",
        "еще",
        "ну",
        "и",
        "а",
        "при",
        "возбуждении",
        "возбуждение",
        "бесполезно",
        "бесполезен",
    }
)


def _is_short_topic_followup(text: str) -> bool:
    """Односложное уточнение внутри текущей темы («почему», «откуда», «а при …»)."""
    t = (text or "").strip().lower()
    if not t or len(t) > 56:
        return False
    if re.match(r"^а\s+", t) and len(t.split()) <= 8:
        return True
    tl = strip_trailing_sentence_punct(t)
    if tl in _SHORT_TOPIC_FOLLOWUPS:
        return True
    words = [re.sub(r"[^\wё]+", "", w) for w in tl.split() if w]
    if 1 <= len(words) <= 3 and all(w in _SHORT_TOPIC_FOLLOWUPS for w in words):
        return True
    try:
        from core.prompt_routing import text_looks_dialog_followup_cue

        if text_looks_dialog_followup_cue(text):
            return True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'behavior_store', e, exc_info=True)
    return False


def _is_anaphora(text: str) -> bool:
    """Проверить, является ли текст анафорой (продолжением темы, не новой)."""
    if not text:
        return False
    if _is_short_topic_followup(text):
        return True
    t = text.strip().lower()
    # Начинается с личного/притяжательного местоимения
    if re.match(
        r"^(он[ао]?|они?|его|её|их|ему|ей|ним|ней|нём|"
        r"это[т]?|эта|эти|такой|такая|такие|"
        r"после\s+(этого|его|её|их)|"
        r"про\s+(это|него|неё|них))",
        t,
    ):
        return True
    # Короткий текст (<6 слов) с отсылочным словом внутри
    words = t.split()
    if len(words) <= 6:
        ref_words = {"он", "она", "оно", "они", "его", "её", "это", "этот",
                     "том", "тем", "нему", "ней", "них", "нём", "ним",
                     "ему", "ей", "им"}
        if any(w in ref_words for w in words):
            return True
    return False


def topic_tracking_for_turn(
    user_text: str,
    stored: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Тема для промпта **текущего** хода (до ответа ассистента).

    update_after_turn пишет topic_tracking только после ответа — без этого в OpenRouter
    уходит прошлая тема (например «светлячки» при вопросе «почему свет быстрый»).
    """
    base = dict(stored) if isinstance(stored, dict) else {}
    cur = str(base.get("current") or "").strip()
    try:
        from core.dialogue_recheck_anchor import looks_like_recheck_last_answer

        if looks_like_recheck_last_answer(user_text):
            return base
    except Exception as e:
        logger.debug("topic_tracking recheck: %s", e)
    if _is_short_topic_followup(user_text) and cur:
        return base
    delta = _topic_from_text((user_text or "").strip(), cur or None)
    if delta:
        base.update(delta)
    return base


def _topic_from_text(user_text: str, current_topic: Optional[str] = None) -> Dict[str, Any]:
    """Извлечь тему из текста пользователя.

    Если текст — анафора (местоимение, отсылка) и есть текущая тема —
    тема НЕ меняется. Это предотвращает «Его» → «Его» как тему.
    """
    t = (user_text or "").strip()
    if len(t) < 2:
        return {}
    # Если анафора — не меняем тему
    if current_topic and _is_anaphora(t):
        return {}
    line = t.split("\n")[0].strip()[:160]
    return {"current": line, "snippet": t[:100]}


# Global BehaviorStore instance for LRU cache
_behavior_store_instance: Optional['BehaviorStore'] = None
_behavior_store_lock = threading.Lock()


def _get_behavior_store() -> 'BehaviorStore':
    """Get or create a global BehaviorStore instance."""
    global _behavior_store_instance
    if _behavior_store_instance is None:
        with _behavior_store_lock:
            if _behavior_store_instance is None:
                _behavior_store_instance = BehaviorStore()
    return _behavior_store_instance


@lru_cache(maxsize=100)
def _load_recent_messages_cached(user_id: str, group_id: str, limit: int) -> Tuple[Dict[str, Any], ...]:
    """
    Cached load of recent messages only (limited to `limit` messages).
    Returns tuple of message dicts for hashability.
    """
    store = _get_behavior_store()
    path = store._path(user_id, group_id)
    if not os.path.isfile(path):
        return tuple()
    try:
        with store._lock:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return tuple()
            msgs = raw.get("recent_messages")
            if not isinstance(msgs, list):
                return tuple()
            # Normalize and trim to limit
            normalized = normalize_dialogue_message_rows(msgs)
            trimmed = trim_dialogue_messages_paired(normalized, limit)
            return tuple(trimmed)
    except Exception as e:
        logger.debug("cached behavior load failed: %s", e)
        return tuple()


class BehaviorStore:
    """File-backed session store per (user_id, group_id)."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = base_dir or os.getenv("BEHAVIOR_DATA_DIR", DEFAULT_BASE)
        self._lock = threading.Lock()
        self._behavior_dir = os.path.join(self.base_dir, "behavior")
        os.makedirs(self._behavior_dir, exist_ok=True)

    def _path(self, user_id: str, group_id: Optional[str]) -> str:
        from core.safe_paths import resolve_under

        return resolve_under(self._behavior_dir, _session_key(user_id, group_id))

    def _defaults(self) -> Dict[str, Any]:
        return {
            "dialogue_state": {
                "turn_index": 0,
                "mode": "chat",
                "last_intent": "unknown",
            },
            "group_context": {
                "interaction_count": 0,
            },
            "recent_messages": [],
            "topic_tracking": {"current": "", "snippet": ""},
            "last_micro_emotion": {},
            "persona_style_anchor": {},
            "user_facts": {},
            "user_facts_meta": {},
            "pending_facts_confirmation": {},
            "pending_facts_overwrite": {},
            "goals_long_term": [],
            "goals_updated_at": "",
            "last_goal_signal": "",
            "behavior_engine_version": 1,
            "routing_prefs": {},
            "ephemeral_autolearn": {"buckets": {}},
            "dialogue_summary": "",
            "session_first_user_text": "",
            "conversation_style": "balanced",
            "conversation_epoch": {
                "id": 0,
                "started_at": "",
                "last_activity_at": "",
            },
            "cdc_policy": {},
            "self_model": {},
            # Сводка последнего хода для оператора (маршрут + инструмент мозга); не полная трассировка.
            "session_task": {
                "updated_at": "",
                "last_user_excerpt": "",
                "last_intent": "",
                "last_module": "",
                "last_outcome": "",
                "last_tool": "",
                "last_tool_ok": None,
                "last_tool_error": "",
                "last_trace_id": "",
            },
            # Эвристический профиль «что заметила система» (привычки, маршруты) + assistant_view; см. core/user_agent_impression.py
            "user_agent_impression": {},
            # Координаты для погоды (фаза 5): после pin/успешного прогноза — без повторного геокода.
            "weather_anchor": {},
        }

    def _merge_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        d0 = self._defaults()
        out = dict(d0)
        for k, v in data.items():
            if k in d0 and isinstance(d0[k], dict) and isinstance(v, dict):
                merged = dict(d0[k])
                merged.update(v)
                out[k] = merged
            else:
                out[k] = v
        rm = out.get("recent_messages")
        if isinstance(rm, list):
            out["recent_messages"] = normalize_dialogue_message_rows(rm)
        return out

    def load(self, user_id: Optional[str], group_id: Optional[str]) -> Dict[str, Any]:
        if not user_id:
            return self._defaults()
        path = self._path(user_id, group_id)
        with self._lock:
            if not os.path.isfile(path):
                return self._defaults()
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    return self._merge_defaults(raw)
            except Exception as e:
                logger.debug("behavior load failed: %s", e)
        return self._defaults()

    def load_recent_messages(self, user_id: str, group_id: Optional[str], limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Load only the last `limit` messages from behavior store.
        Uses LRU cache to avoid repeated disk reads.
        """
        if not user_id:
            return []
        lim = limit or _get_recent_messages_limit()
        cached = _load_recent_messages_cached(user_id, group_id or "dm", lim)
        return list(cached)

    def iter_session_group_ids(self, user_id: str) -> List[Optional[str]]:
        """Существующие сессии пользователя: None = личка (файл …__dm.json), иначе id чата группы."""
        if not user_id:
            return []
        safe_u = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(user_id))[:128]
        prefix = f"{safe_u}__"
        out: List[Optional[str]] = []
        try:
            for name in sorted(os.listdir(self._behavior_dir)):
                if not name.endswith(".json") or not name.startswith(prefix):
                    continue
                gpart = name[len(prefix) : -len(".json")]
                out.append(None if gpart == "dm" else str(gpart))
        except OSError:
            return []
        return out

    def load_user_profile_aggregate(self, user_id: str) -> Dict[str, Any]:
        """
        Объединить user_facts (и стиль) из всех файлов сессий для отображения профиля из лички.
        Диалог в группе пишет в отдельный JSON; без агрегации /me в ЛС остаётся пустым.
        """
        ids = self.iter_session_group_ids(user_id)
        if not ids:
            return self._defaults()

        def _field_ts(meta: Dict[str, Any], field: str) -> str:
            info = meta.get(field)
            if not isinstance(info, dict):
                return ""
            return str(info.get("updated_at") or "")

        merged_facts: Dict[str, Any] = {}
        merged_meta: Dict[str, Any] = {}
        best_anchor: Dict[str, Any] = {}
        best_anchor_rank: tuple[int, str] = (-1, "")

        for gid in ids:
            rec = self.load(user_id, gid)
            facts = rec.get("user_facts") if isinstance(rec.get("user_facts"), dict) else {}
            meta = rec.get("user_facts_meta") if isinstance(rec.get("user_facts_meta"), dict) else {}
            for field, value in facts.items():
                if field not in merged_facts or _field_ts(meta, field) >= _field_ts(merged_meta, field):
                    merged_facts[field] = value
                    if field in meta:
                        merged_meta[field] = meta[field]

            gc = rec.get("group_context") if isinstance(rec.get("group_context"), dict) else {}
            ic = int(gc.get("interaction_count") or 0)
            fl = str(rec.get("facts_last_update") or "")
            anchor = rec.get("persona_style_anchor") if isinstance(rec.get("persona_style_anchor"), dict) else {}
            if anchor:
                rank = (ic, fl)
                if rank >= best_anchor_rank:
                    best_anchor_rank = rank
                    best_anchor = dict(anchor)

        out = self._defaults()
        out["user_facts"] = merged_facts
        out["user_facts_meta"] = merged_meta
        out["persona_style_anchor"] = best_anchor
        return out

    def save(self, user_id: str, group_id: Optional[str], record: Dict[str, Any]) -> None:
        path = self._path(user_id, group_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def patch_session_task(self, user_id: Optional[str], group_id: Optional[str], patch: Dict[str, Any]) -> None:
        """Частично обновить session_task (последний маршрут или вызов инструмента мозга)."""
        if not user_id or not isinstance(patch, dict) or not patch:
            return
        path = self._path(user_id, group_id)
        with self._lock:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    rec = self._merge_defaults(raw) if isinstance(raw, dict) else self._defaults()
                except Exception as e:
                    logger.debug("patch_session_task load: %s", e)
                    rec = self._defaults()
            else:
                rec = self._defaults()
            cur = dict(rec.get("session_task") or {})
            for k, v in patch.items():
                if v is not None:
                    cur[k] = v
            cur["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rec["session_task"] = cur
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)

    def patch_session_fields(
        self,
        user_id: Optional[str],
        group_id: Optional[str],
        patch: Dict[str, Any],
    ) -> None:
        """Частично обновить поля верхнего уровня behavior (weather_anchor и т.п.)."""
        if not user_id or not isinstance(patch, dict) or not patch:
            return
        path = self._path(user_id, group_id)
        with self._lock:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    rec = self._merge_defaults(raw) if isinstance(raw, dict) else self._defaults()
                except Exception as e:
                    logger.debug("patch_session_fields load: %s", e)
                    rec = self._defaults()
            else:
                rec = self._defaults()
            for k, v in patch.items():
                if v is not None:
                    rec[k] = v
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def update_after_turn(
        self,
        user_id: str,
        group_id: Optional[str],
        user_text: str,
        assistant_text: str,
        dialogue_patch: Optional[Dict[str, Any]] = None,
        group_patch: Optional[Dict[str, Any]] = None,
        blended_style: Optional[Dict[str, Any]] = None,
        micro_emotion: Optional[Dict[str, Any]] = None,
        telegram_is_admin: bool = False,
        turn_meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[DialogueCompactPending]]:
        rec = self.load(user_id, group_id)
        pending_compact: Optional[DialogueCompactPending] = None
        try:
            merge_routing_prefs_from_turn(rec, user_text or "")
        except Exception as e:
            logger.debug("routing_prefs merge: %s", e)
        try:
            from core.policy_memory_runtime import update_policy_slots_on_user_turn

            recent = rec.get("recent_messages") if isinstance(rec.get("recent_messages"), list) else []
            update_policy_slots_on_user_turn(
                rec,
                user_text or "",
                recent,
                user_id=str(user_id or ""),
            )
        except Exception as e:
            logger.debug("policy_slots user turn: %s", e)
        msgs: List[Dict[str, Any]] = rec.get("recent_messages") or []
        if not isinstance(msgs, list):
            msgs = []
        if (user_text or "").strip() and not (rec.get("session_first_user_text") or "").strip():
            rec["session_first_user_text"] = (user_text or "")[:2000]

        now_unix = int(time.time())
        urow: Dict[str, Any] = {"role": "user", "text": (user_text or "")[:2000], "ts_unix": now_unix}
        if isinstance(turn_meta, dict) and turn_meta.get("telegram_message_date_unix") is not None:
            try:
                urow["telegram_ts"] = int(turn_meta["telegram_message_date_unix"])
                urow["ts_unix"] = int(turn_meta["telegram_message_date_unix"])
            except (TypeError, ValueError):
                pass
        if isinstance(turn_meta, dict) and turn_meta.get("telegram_message_id") is not None:
            try:
                urow["telegram_message_id"] = int(turn_meta["telegram_message_id"])
            except (TypeError, ValueError):
                pass
        try:
            from core.conversation_epoch import get_epoch_id, touch_activity

            urow["conversation_epoch_id"] = get_epoch_id(rec)
            touch_activity(rec)
        except Exception as e:
            logger.debug("conversation_epoch turn: %s", e)
        msgs.append(urow)
        msgs.append({"role": "assistant", "text": (assistant_text or "")[:8000], "ts_unix": int(time.time())})
        try:
            from core.dialogue_slots import on_assistant_reply

            on_assistant_reply(rec, assistant_text or "", user_text=user_text or "")
        except Exception as e:
            logger.debug("dialogue_slots on_assistant: %s", e)
        max_msgs = max(4, int(os.getenv("DIALOGUE_MEMORY_MAX", "12")))
        if group_id:
            try:
                gmax = int(load_group_chat_policy().get("group_memory_max") or 12)
                max_msgs = max(max_msgs, max(4, min(40, gmax)))
            except Exception as e:
                logger.debug('%s optional failed: %s', 'behavior_store', e, exc_info=True)
        pre_norm = normalize_dialogue_message_rows(msgs)
        trimmed = trim_dialogue_messages_paired(pre_norm, max_msgs)
        overflow_count = max(0, len(pre_norm) - len(trimmed))
        if overflow_count > 0 and _dialogue_compact_llm_enabled():
            overflow_messages = pre_norm[:overflow_count]
            if overflow_messages:
                try:
                    max_sum = max(400, int(os.getenv("DIALOGUE_SUMMARY_MAX_CHARS", "2000")))
                except ValueError:
                    max_sum = 2000
                summary_before = str(rec.get("dialogue_summary") or "")
                pending_compact = {
                    "user_id": str(user_id),
                    "group_id": group_id,
                    "summary_before": summary_before,
                    "summary_after_snippet": summary_before,
                    "overflow_messages": overflow_messages,
                    "max_summary": max_sum,
                }
        msgs = trimmed
        rec["recent_messages"] = msgs
        try:
            from core.context_compression import deprioritize_failed_dialogue_rows

            rec["recent_messages"] = deprioritize_failed_dialogue_rows(rec.get("recent_messages"))
        except Exception as e:
            logger.debug("deprioritize_failed_dialogue: %s", e)
        rec["recent_messages"] = compress_recent_dialogue(rec.get("recent_messages"))
        try:
            from core.context_tool_trim import trim_tool_outputs_in_dialogue

            rec["recent_messages"] = trim_tool_outputs_in_dialogue(rec.get("recent_messages"))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'behavior_store', e, exc_info=True)
        rec["dialogue_summary"] = compress_dialogue_summary(rec.get("dialogue_summary"))

        ds = dict(rec.get("dialogue_state") or {})
        ds["turn_index"] = int(ds.get("turn_index", 0)) + 1
        if dialogue_patch:
            ds.update(dialogue_patch)

        # Context Anchors: обновить хранилище сущностей (сырые тексты, ДО обрезания)
        try:
            from core.brain.context_anchors import update_anchor_store

            ds_entities = ds.get("anchor_entities")
            new_entities = update_anchor_store(
                existing=ds_entities,
                user_text=user_text or "",
                assistant_text=assistant_text or "",
                turn_index=int(ds.get("turn_index", 0)),
            )
            ds["anchor_entities"] = new_entities
        except Exception as e:
            logger.debug("anchor_store update: %s", e)

        rec["dialogue_state"] = ds
        try:
            from core.news_reply import persist_news_digest_from_assistant_reply

            persist_news_digest_from_assistant_reply(
                assistant_text or "",
                persisted=rec,
                context={"user_id": user_id, "group_id": group_id},
            )
        except Exception as e:
            logger.debug("persist news digest update_after_turn: %s", e)

        gc = dict(rec.get("group_context") or {})
        gc["interaction_count"] = int(gc.get("interaction_count", 0)) + 1
        if group_id:
            gc["group_id"] = group_id
        if group_patch:
            gc.update(group_patch)
        rec["group_context"] = gc

        current_topic = None
        current_tt = rec.get("topic_tracking")
        if isinstance(current_tt, dict):
            current_topic = current_tt.get("current")
        topic_delta = _topic_from_text(user_text, current_topic)
        if topic_delta:
            tt = dict(rec.get("topic_tracking") or {})
            tt.update(topic_delta)
            rec["topic_tracking"] = tt

        if blended_style:
            rec["persona_style_anchor"] = blended_style
        if micro_emotion is not None:
            rec["last_micro_emotion"] = micro_emotion

        try:
            from core.ephemeral_autolearn import process_turn_for_autolearn

            process_turn_for_autolearn(
                rec,
                user_text or "",
                assistant_text or "",
                user_id=user_id,
                group_id=group_id,
                telegram_is_admin=bool(telegram_is_admin),
            )
        except Exception as e:
            logger.debug("ephemeral_autolearn: %s", e)

        try:
            from core.message_archive import append_turn_to_message_archive

            append_turn_to_message_archive(user_id, group_id, dict(urow), assistant_text or "")
        except Exception as e:
            logger.debug("message_archive append: %s", e)

        # Batch continuation: определяем неотвеченные пункты и сохраняем
        # handle_batch_continuation сам внутри проверяет _detect_batch + get_pending
        # и возвращает [] за микросекунды, если нечего делать
        try:
            from core.batch_continuation import handle_batch_continuation
            handle_batch_continuation(rec, user_text or "", assistant_text or "")
        except Exception as e:
            logger.debug("batch_continuation: %s", e)

        try:
            from core.timezone_inference import ensure_timezone_in_user_facts

            _uf = rec.get("user_facts")
            if isinstance(_uf, dict):
                ensure_timezone_in_user_facts(_uf)
        except Exception as e:
            logger.debug("ensure_timezone_in_user_facts: %s", e)

        self.save(user_id, group_id, rec)
        return rec, pending_compact