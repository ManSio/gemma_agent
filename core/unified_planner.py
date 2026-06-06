from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

from modules.skills.skill_router import resolve_skill_intent_sync

# manifest / allowlist могут дать chat-orchestrator или chat_orchestrator — выбираем тот, что реально в allowed.
_DIALOG_KEYS_ORDER = ("chat-orchestrator", "chat_orchestrator", "smartchat")


def pick_dialog_module(allowed_modules: Set[str]) -> Optional[str]:
    for k in _DIALOG_KEYS_ORDER:
        if k in allowed_modules:
            return k
    return None


@dataclass
class PlannerDecision:
    module_name: str
    intent: str
    reason: str
    skill_name: str = ""
    fallback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_name": self.module_name,
            "intent": self.intent,
            "reason": self.reason,
            "skill_name": self.skill_name,
            "fallback": self.fallback,
        }


class UnifiedPlanner:
    """
    Single decision engine for module + skill hints.
    Keeps routing contract unchanged: returns module_name for PlanStep.
    """

    def decide(
        self,
        *,
        text: str,
        allowed_modules: Set[str],
        route_command,
        detect_intent,
        select_module,
        input_meta: Optional[Dict[str, Any]] = None,
        knowledge_hint: Optional[Dict[str, Any]] = None,
    ) -> PlannerDecision:
        raw = (text or "").strip()
        meta = input_meta or {}
        if isinstance(meta.get("telegram_location"), dict):
            dm = pick_dialog_module(allowed_modules)
            if dm:
                return PlannerDecision(
                    module_name=dm,
                    intent="geo",
                    reason="telegram_location_in_meta",
                    skill_name="",
                )
        kh = knowledge_hint if isinstance(knowledge_hint, dict) else {}
        kh_policy = str(kh.get("policy") or "")
        kh_conf = float(kh.get("confidence") or 0.0)
        file_ctx = meta.get("file_context") if isinstance(meta, dict) else {}
        has_image = isinstance(file_ctx, dict) and file_ctx.get("file_type") == "image"

        if raw.startswith("/"):
            mk = route_command(raw, allowed_modules)
            if mk and mk in allowed_modules:
                return PlannerDecision(module_name=mk, intent="command", reason="slash_command")
            # Неизвестная /команда — не обрываем пайплайн: отдаём в диалог (мозг / generate_module и т.д.)
            cmd0 = raw.split()[0].lstrip("/").split("@")[0].lower()
            passthrough = {
                "generate_module",
                "gen_module",
                "selfprogramming",
                "self_programming",
            }
            if cmd0 in passthrough:
                dm = pick_dialog_module(allowed_modules)
                if dm:
                    return PlannerDecision(
                        module_name=dm,
                        intent="command",
                        reason="slash_passthrough_plugin_gen",
                        fallback=False,
                    )
            try:
                from core.command_catalog import is_core_exclusive_token

                if is_core_exclusive_token(cmd0):
                    return PlannerDecision(
                        module_name="__fallback__",
                        intent="command",
                        reason="core_exclusive_pending",
                        fallback=True,
                    )
            except Exception:
                pass
            return PlannerDecision(module_name="__fallback__", intent="command", reason="unknown_command", fallback=True)

        intent = detect_intent(raw) if raw else "empty"
        skill = resolve_skill_intent_sync(raw) or ("image_skill" if has_image else "")
        if not skill and kh_policy == "fresh_trusted_tagged" and kh_conf >= 0.75:
            if "weather" in raw.lower():
                skill = "weather"
            elif "currency" in raw.lower() or "курс" in raw.lower():
                skill = "currency"
        has_doc = isinstance(meta.get("document_intake"), dict) and bool(meta.get("document_intake"))
        has_code = isinstance(meta.get("code_intake"), dict) and bool(meta.get("code_intake"))

        mk = select_module(intent, allowed_modules)
        if mk and mk in allowed_modules:
            reason = "intent_module_match"
            if kh_policy:
                reason = f"{reason}|kh:{kh_policy}"
            return PlannerDecision(module_name=mk, intent=intent, reason=reason, skill_name=skill)

        # If rich attachments/context exist, prefer chat orchestrator for unified handling.
        if has_image or has_doc or has_code:
            dm = pick_dialog_module(allowed_modules)
            if dm:
                return PlannerDecision(
                    module_name=dm,
                    intent=intent,
                    reason="rich_context_chat_orchestrator" + (f"|kh:{kh_policy}" if kh_policy else ""),
                    skill_name=skill,
                )

        # Prefer chat orchestrator as unified fallback target if available.
        dm = pick_dialog_module(allowed_modules)
        if dm:
            return PlannerDecision(
                module_name=dm,
                intent=intent,
                reason="chat_orchestrator_fallback" + (f"|kh:{kh_policy}" if kh_policy else ""),
                skill_name=skill,
            )

        return PlannerDecision(module_name="__fallback__", intent=intent, reason="no_module_available", skill_name=skill, fallback=True)
