"""
Digital Twin Module — профиль «цифрового двойника» (персистентно на диске).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.getcwd(), "data", "digital_twins.json")


class DigitalTwinModule:
    """Модуль цифрового двойника с сохранением между перезапусками."""

    def __init__(self, storage_path: Optional[str] = None) -> None:
        self._path = storage_path or os.getenv("DIGITAL_TWINS_PATH", _DEFAULT_PATH)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self.twins: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self._path):
            self.twins = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.twins = raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning("digital_twin load failed: %s", e)
            self.twins = {}

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.twins, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("digital_twin save failed: %s", e)

    def get_digital_twin(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.twins.get(str(user_id))

    def update_twin(self, user_id: str, data: Dict[str, Any]) -> bool:
        uid = str(user_id)
        if uid not in self.twins:
            self.twins[uid] = {}
        self.twins[uid].update(data)
        self._save()
        return True

    def get_learning_profile(self, user_id: str) -> Dict[str, Any]:
        twin = self.twins.get(str(user_id), {})
        return twin.get("learning_profile") or {}

    def user_snapshot_for_agent(self, user_id: str, group_id: str = "") -> Dict[str, Any]:
        """
        Сводка для ответа пользователю: профиль двойника + факты/самомодель из behavior_store.
        Не заменяет Mem0 и архив — только то, что уже агрегировано локально.
        """
        uid = str(user_id or "").strip()
        if not uid:
            return {"ok": False, "error": "user_id required"}
        gid: Optional[str] = str(group_id or "").strip() or None

        twin = self.twins.get(uid) or {}
        lp = twin.get("learning_profile") if isinstance(twin.get("learning_profile"), dict) else {}

        behavior: Dict[str, Any] = {}
        psych_excerpt: Dict[str, Any] = {}
        user_profile_excerpt: Dict[str, Any] = {}
        impression_hint = ""
        try:
            from core.behavior_store import BehaviorStore
            from core.user_agent_impression import impression_excerpt_for_snapshot

            rec = BehaviorStore().load(uid, gid)
            uf = rec.get("user_facts") if isinstance(rec.get("user_facts"), dict) else {}
            fact_keys: List[str] = sorted(uf.keys(), key=lambda k: str(k).lower())[:40]
            sm = rec.get("self_model") if isinstance(rec.get("self_model"), dict) else {}
            st = rec.get("session_task") if isinstance(rec.get("session_task"), dict) else {}
            behavior = {
                "dialogue_summary": str(rec.get("dialogue_summary") or "").strip()[:1500],
                "conversation_style": str(rec.get("conversation_style") or "").strip(),
                "dialogue_state": {
                    "mode": str((rec.get("dialogue_state") or {}).get("mode") or ""),
                    "last_intent": str((rec.get("dialogue_state") or {}).get("last_intent") or ""),
                },
                "session_task_excerpt": {
                    "last_module": str(st.get("last_module") or ""),
                    "last_intent": str(st.get("last_intent") or ""),
                    "last_outcome": str(st.get("last_outcome") or ""),
                    "last_tool": str(st.get("last_tool") or ""),
                },
                "user_fact_keys_sample": fact_keys,
                "user_facts_count": len(uf),
                "agent_self_model_excerpt": {k: sm[k] for k in list(sm.keys())[:12] if k in sm},
                "self_model_excerpt": {k: sm[k] for k in list(sm.keys())[:12] if k in sm},
                "self_model_note_ru": (
                    "Поле agent_self_model — самомодель ассистента (маршрут, уверенность), не путать с профилем пользователя."
                ),
            }
            user_profile_excerpt, impression_hint = impression_excerpt_for_snapshot(rec)
        except Exception as e:
            behavior = {"error": str(e)}

        try:
            from core.psychology_engine import PsychologyEngineModule

            prof = PsychologyEngineModule().get_psychology_profile(uid)
            if isinstance(prof, dict):
                la = prof.get("last_analysis") if isinstance(prof.get("last_analysis"), dict) else {}
                psych_excerpt = {
                    "stress_streak": prof.get("stress_streak"),
                    "last_sentiment": la.get("sentiment"),
                    "keywords": la.get("keywords") if isinstance(la.get("keywords"), list) else [],
                    "note_ru": "Эвристика /psych (не диагноз).",
                }
        except Exception:
            psych_excerpt = {}

        return {
            "ok": True,
            "user_id": uid,
            "learning_profile_excerpt": {
                "interests": (lp.get("interests") or [])[:30] if isinstance(lp.get("interests"), list) else [],
                "goals": (lp.get("goals") or [])[:20] if isinstance(lp.get("goals"), list) else [],
                "preferred_explanation_style": lp.get("preferred_explanation_style"),
                "last_updated": lp.get("last_updated"),
            },
            "behavior_session": behavior,
            "psychology_profile_excerpt": psych_excerpt,
            "user_digital_profile": user_profile_excerpt,
            "hint": (
                "Интересы в learning_profile — из модуля двойника; факты пользователя — user_facts в behavior_session. "
                "user_digital_profile — привычки и эвристический «взгляд ассистента» (что заметила система по действиям); "
                "agent_self_model — внутренняя самомодель бота. Для файлов — UserKnowledgeArchive.personal_library_list. "
                + (impression_hint or "")
            ),
        }
