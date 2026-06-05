from __future__ import annotations

from typing import Any, Dict, Optional


class UserManagementModule:
    def __init__(self, behavior_store: Any, user_facts_manager: Any, user_system: Any = None, digital_twin: Any = None):
        self.behavior_store = behavior_store
        self.user_facts_manager = user_facts_manager
        self.user_system = user_system
        self.digital_twin = digital_twin

    def ensure_user(self, user_id: str) -> None:
        if self.user_system and hasattr(self.user_system, "get_user") and hasattr(self.user_system, "update_user"):
            if not self.user_system.get_user(user_id):
                self.user_system.update_user(user_id, {"user_id": user_id, "source": "telegram"})
        if self.digital_twin and hasattr(self.digital_twin, "update_twin"):
            self.digital_twin.update_twin(user_id, {"user_id": user_id})

    def _profile_record(self, user_id: str, group_id: Optional[str]) -> Dict[str, Any]:
        if group_id is not None:
            return self.behavior_store.load(user_id, group_id)
        return self.behavior_store.load_user_profile_aggregate(user_id)

    def me_summary(self, user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        rec = self._profile_record(user_id, group_id)
        return {
            "user_id": user_id,
            "facts": rec.get("user_facts", {}),
            "preferences": rec.get("persona_style_anchor", {}),
        }

    def facts_summary(self, user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        rec = self._profile_record(user_id, group_id)
        return {"facts": rec.get("user_facts", {}), "facts_meta": rec.get("user_facts_meta", {})}

    def forget_field(self, user_id: str, field: str, group_id: Optional[str] = None) -> bool:
        def _one(gid: Optional[str]) -> bool:
            rec = self.behavior_store.load(user_id, gid)
            facts = dict(rec.get("user_facts", {}))
            meta = dict(rec.get("user_facts_meta", {}))
            if field not in facts:
                return False
            facts.pop(field, None)
            info = dict(meta.get(field, {}))
            info["revoked"] = True
            meta[field] = info
            rec["user_facts"] = facts
            rec["user_facts_meta"] = meta
            self.behavior_store.save(user_id, gid, rec)
            return True

        if group_id is not None:
            ok = _one(group_id)
        else:
            ok = any(_one(gid) for gid in self.behavior_store.iter_session_group_ids(user_id))
        if ok and field in {"city", "country"} and self.digital_twin and hasattr(
            self.digital_twin, "get_digital_twin"
        ) and hasattr(self.digital_twin, "update_twin"):
            tw = self.digital_twin.get_digital_twin(user_id) or {}
            loc = dict(tw.get("location") or {}) if isinstance(tw.get("location"), dict) else {}
            loc.pop("city" if field == "city" else "country", None)
            self.digital_twin.update_twin(user_id, {"location": loc})
        return ok

    def facts_refresh(self, user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        before_rec = self._profile_record(user_id, group_id)
        if group_id is not None:
            self.user_facts_manager.process_turn(user_id, group_id, "")
        else:
            for gid in self.behavior_store.iter_session_group_ids(user_id):
                self.user_facts_manager.process_turn(user_id, gid, "")
        after_rec = self._profile_record(user_id, group_id)
        return {"before": before_rec.get("user_facts", {}), "after": after_rec.get("user_facts", {})}

    def facts_reset(self, user_id: str, group_id: Optional[str] = None) -> None:
        def _reset_one(gid: Optional[str]) -> None:
            rec = self.behavior_store.load(user_id, gid)
            rec["user_facts"] = {}
            rec["user_facts_meta"] = {}
            rec["pending_facts_confirmation"] = {}
            rec["pending_facts_overwrite"] = {}
            self.behavior_store.save(user_id, gid, rec)

        if group_id is not None:
            _reset_one(group_id)
        else:
            ids = self.behavior_store.iter_session_group_ids(user_id)
            if not ids:
                _reset_one(None)
            else:
                for gid in ids:
                    _reset_one(gid)
