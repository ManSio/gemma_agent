import os
import unittest

from core.brain import prompt_pack as pp
from core.brain.constants import BRAIN_STATIC_FORMAT
from core.brain.prompt_pack import assemble_brain_user_prompt, assemble_with_budget, estimate_tokens_approx
from core.prompt_assembly import PromptAssemblyTier


class PromptPackTests(unittest.TestCase):
    def test_estimate_tokens_positive(self):
        self.assertGreaterEqual(estimate_tokens_approx("abcd"), 1)

    def test_clip_soft_prefers_word_boundary(self):
        long = "одно " + "слово " * 200 + "хвост"
        out = pp._clip(long, 120)
        self.assertTrue(out.endswith(" …") or out.endswith("..."))
        self.assertNotIn("хвост", out)
        self.assertFalse(out[:-4].endswith("сл"))

    def test_clip_soft_never_exceeds_limit(self):
        blob = "word " * 120
        old = os.environ.get("BRAIN_PROMPT_CLIP_MODE")
        try:
            os.environ["BRAIN_PROMPT_CLIP_MODE"] = "soft"
            for n in (40, 100, 500):
                out = pp._clip(blob, n)
                self.assertLessEqual(len(out), n, msg=f"n={n} got len={len(out)}")
        finally:
            if old is None:
                os.environ.pop("BRAIN_PROMPT_CLIP_MODE", None)
            else:
                os.environ["BRAIN_PROMPT_CLIP_MODE"] = old

    def test_clip_none_returns_full(self):
        old = os.environ.get("BRAIN_PROMPT_CLIP_MODE")
        try:
            os.environ["BRAIN_PROMPT_CLIP_MODE"] = "none"
            long = "x" * 5000
            self.assertEqual(pp._clip(long, 100), long)
        finally:
            if old is None:
                os.environ.pop("BRAIN_PROMPT_CLIP_MODE", None)
            else:
                os.environ["BRAIN_PROMPT_CLIP_MODE"] = old

    def test_static_head_is_first(self):
        """Первые блоки: System → Tools → Format (статичные, System намеренно дублируется)."""
        p = {
            "system_prompt_for_llm": "SYSTEM_TEXT",
            "agent_inst": "AGENT_TEXT",
            "intent_addon": "",
            "user_text": "привет",
            "user_id": "1",
            "memory_facts": [],
            "recent_dialogue": [],
            "message_archive": [],
            "dialogue_summary": "",
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "/start",
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "",
            "ephemeral_lessons": "",
            "task_facts": {},
            "knowledge_summary": "",
            "knowledge_hot": "",
            "external_hint": "",
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": [],
            "urls_in_message": [],
            "group_chat_addon": "",
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {},
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\n",
        }
        s = assemble_brain_user_prompt(PromptAssemblyTier.FULL, p, collapse_level=0)
        # Static head: System → Tools → Format → User message (System намеренно дублирует messages[0])
        self.assertIn("System:", s)
        self.assertIn("Tools:", s)
        self.assertIn("Format:", s)
        # System before Tools before Format before User message
        pos_sys = s.index("System:")
        pos_tools = s.index("Tools:")
        pos_fmt = s.index("Format:")
        pos_user = s.index("User message:")
        self.assertLess(pos_sys, pos_tools)
        self.assertLess(pos_tools, pos_fmt)
        self.assertLess(pos_fmt, pos_user)
        # Output format text must be present
        self.assertIn("Отвечай на русском", s)
        self.assertIn("привет", s)

    def test_deterministic_static_head(self):
        """При одинаковом system_prompt_for_llm начало промпта должно быть идентичным."""
        p1 = {
            "system_prompt_for_llm": "SYSTEM_A",
            "agent_inst": "a1",
            "user_text": "msg1",
            "user_id": "1",
            "memory_facts": [],
            "recent_dialogue": [{"role": "user", "text": "diff1"}],
            "message_archive": [],
            "dialogue_summary": "",
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "",
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "",
            "ephemeral_lessons": "",
            "task_facts": {},
            "knowledge_summary": "",
            "knowledge_hot": "",
            "external_hint": "",
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": [],
            "urls_in_message": [],
            "group_chat_addon": "",
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {},
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\n",
        }
        p2 = dict(p1)
        p2["user_text"] = "msg2"
        p2["recent_dialogue"] = [{"role": "user", "text": "diff2"}]
        s1 = assemble_brain_user_prompt(PromptAssemblyTier.FULL, p1, collapse_level=0)
        s2 = assemble_brain_user_prompt(PromptAssemblyTier.FULL, p2, collapse_level=0)
        # First 600 chars must be identical (static head)
        self.assertEqual(s1[:600], s2[:600])

    def test_goal_hints_present(self):
        """Goal hints appear in Task/Goal block (planning profile keeps goal_hints)."""
        from core.brain.prompt_pack import _build_dynamic_context, _labels, _full_limits

        base = {
            "system_prompt_for_llm": "x",
            "agent_inst": "a",
            "intent_addon": "",
            "user_text": "hi",
            "user_id": "1",
            "memory_facts": [],
            "recent_dialogue": [],
            "message_archive": [],
            "dialogue_summary": "",
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "/start",
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "",
            "ephemeral_lessons": "",
            "task_facts": {},
            "knowledge_summary": "",
            "knowledge_hot": "",
            "external_hint": "",
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": [],
            "urls_in_message": [],
            "group_chat_addon": "",
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {
                "goal_ids": ["fast_coding_help"],
                "active_goals": [{"id": "fast_coding_help", "text": "help code", "status": "active", "weight": 0.7}],
                "mission": "test mission",
            },
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\n",
        }
        s = _build_dynamic_context(
            base, _labels(), _full_limits(0), PromptAssemblyTier.FULL, 0,
            profile="planning", intent="general",
        )
        self.assertIn("- goal_hints:", s)
        self.assertIn("fast_coding_help", s)
        # Task/Goal block must come after Context block (EN labels)
        self.assertIn("- goal_hints: focus=active: fast_coding_help", s)

    def test_full_budget_collapses(self):
        p = {
            "system_prompt_for_llm": "s",
            "agent_inst": "a",
            "intent_addon": "",
            "user_text": "u",
            "user_id": "1",
            "memory_facts": list(range(40)),
            "recent_dialogue": [{"role": "user", "text": "x"}] * 10,
            "message_archive": [],
            "dialogue_summary": "d" * 2000,
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "c" * 5000,
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "o" * 9000,
            "ephemeral_lessons": "e" * 9000,
            "task_facts": {},
            "knowledge_summary": "k" * 500,
            "knowledge_hot": "",
            "external_hint": "x" * 5000,
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": ["T1", "T2"],
            "urls_in_message": [],
            "group_chat_addon": "g" * 8000,
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {},
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\nlong scaffold\n",
        }
        old = os.environ.get("BRAIN_USER_PROMPT_BUDGET_CHARS")
        try:
            os.environ["BRAIN_USER_PROMPT_BUDGET_CHARS"] = "16000"
            text, meta = assemble_with_budget(PromptAssemblyTier.FULL, p)
            self.assertLessEqual(len(text), 16000 + 3000)
            self.assertIn("collapse_level", meta)
            self.assertFalse(meta.get("budget_exceeded"))
        finally:
            if old is None:
                os.environ.pop("BRAIN_USER_PROMPT_BUDGET_CHARS", None)
            else:
                os.environ["BRAIN_USER_PROMPT_BUDGET_CHARS"] = old

    def test_adaptive_budget_compacts_non_deep_full(self):
        old_full = os.environ.get("BRAIN_USER_PROMPT_BUDGET_CHARS")
        old_compact = os.environ.get("BRAIN_USER_PROMPT_BUDGET_CHARS_COMPACT")
        try:
            os.environ["BRAIN_USER_PROMPT_BUDGET_CHARS"] = "16000"
            os.environ["BRAIN_USER_PROMPT_BUDGET_CHARS_COMPACT"] = "13000"
            p = {
                "dialogue_state": {"task_tier": "shallow", "last_intent": "general"},
                "user_text": "короткий запрос",
            }
            b = pp._adaptive_budget_for_parts(PromptAssemblyTier.FULL, p, 16000)
            self.assertEqual(b, 13000)
            p_deep = {
                "dialogue_state": {"task_tier": "deep", "last_intent": "reasoning"},
                "user_text": "x" * 200,
            }
            b_deep = pp._adaptive_budget_for_parts(PromptAssemblyTier.FULL, p_deep, 16000)
            self.assertEqual(b_deep, 16000)
        finally:
            if old_full is None:
                os.environ.pop("BRAIN_USER_PROMPT_BUDGET_CHARS", None)
            else:
                os.environ["BRAIN_USER_PROMPT_BUDGET_CHARS"] = old_full
            if old_compact is None:
                os.environ.pop("BRAIN_USER_PROMPT_BUDGET_CHARS_COMPACT", None)
            else:
                os.environ["BRAIN_USER_PROMPT_BUDGET_CHARS_COMPACT"] = old_compact

    def test_recent_messages_limited_to_5(self):
        """recent_dialogue tail respects profile recent_count (standard=10)."""
        from core.brain.profile_registry import get_profile

        recent_n = int(get_profile("standard").recent_count or 10)
        msg_count = recent_n + 5
        p = {
            "system_prompt_for_llm": "s",
            "agent_inst": "a",
            "intent_addon": "",
            "user_text": "hi",
            "user_id": "1",
            "memory_facts": [],
            "recent_dialogue": [
                {"role": "user", "text": f"msg{i}"} for i in range(msg_count)
            ],
            "message_archive": [],
            "dialogue_summary": "",
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "/start",
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "",
            "ephemeral_lessons": "",
            "task_facts": {},
            "knowledge_summary": "",
            "knowledge_hot": "",
            "external_hint": "",
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": [],
            "urls_in_message": [],
            "group_chat_addon": "",
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {},
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\n",
        }
        from core.brain.prompt_pack import _build_dynamic_context, _labels, _full_limits

        s = _build_dynamic_context(
            p, _labels(), _full_limits(0), PromptAssemblyTier.FULL, 0,
            profile="standard", intent="general",
        )
        cutoff = msg_count - recent_n
        self.assertNotIn("msg0", s)
        if cutoff > 0:
            self.assertNotIn(f"msg{cutoff - 1}", s)
        self.assertIn(f"msg{cutoff}", s)
        self.assertIn(f"msg{msg_count - 1}", s)

    def test_archive_is_fifo(self):
        """Archive should only show last N entries, FIFO order."""
        from core.brain.profile_registry import get_profile

        arch_n = int(get_profile("standard").archive_count or 3)
        p = {
            "system_prompt_for_llm": "s",
            "agent_inst": "a",
            "intent_addon": "",
            "user_text": "hi",
            "user_id": "1",
            "memory_facts": [],
            "recent_dialogue": [],
            "message_archive": [
                {"role": "user", "text": f"arch{i}"} for i in range(30)
            ],
            "dialogue_summary": "",
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "/start",
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "",
            "ephemeral_lessons": "",
            "task_facts": {},
            "knowledge_summary": "",
            "knowledge_hot": "",
            "external_hint": "",
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": [],
            "urls_in_message": [],
            "group_chat_addon": "",
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {},
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\n",
        }
        from core.brain.prompt_pack import _build_dynamic_context, _labels, _full_limits

        s = _build_dynamic_context(
            p, _labels(), _full_limits(0), PromptAssemblyTier.FULL, 0,
            profile="standard", intent="general",
        )
        self.assertNotIn("arch0", s)
        self.assertNotIn(f"arch{30 - arch_n - 2}", s)
        self.assertIn(f"arch{30 - arch_n}", s)
        self.assertIn("arch29", s)

    def test_unstable_fields_excluded_from_prompt(self):
        """persona, psychology, twin_profile, predictive_hint, style_hints must NOT appear in prompt."""
        p = {
            "system_prompt_for_llm": "s",
            "agent_inst": "a",
            "intent_addon": "",
            "user_text": "hi",
            "user_id": "1",
            "memory_facts": [],
            "recent_dialogue": [],
            "message_archive": [],
            "dialogue_summary": "",
            "grounding_mini": {},
            "user_facts": {},
            "routing_prefs_hint": "",
            "tcmd_cat": "/start",
            "sess_first": "",
            "pteacher": "",
            "operator_rules": "",
            "ephemeral_lessons": "",
            "task_facts": {},
            "knowledge_summary": "",
            "knowledge_hot": "",
            "external_hint": "",
            "vp_ctx": "",
            "skill_name": None,
            "image_intent": None,
            "skill_output": {},
            "skill_hint": "",
            "ocr_text": "",
            "tools_mode": "auto",
            "tool_names": [],
            "urls_in_message": [],
            "group_chat_addon": "",
            "topic_tracking": {},
            "group_context": {},
            "user_facts_meta": {},
            "missing_facts": [],
            "auto_ask_hint": "",
            "behavior_policy": {},
            "goal_hints": {},
            "blended_stable": {},
            "goal_plan": {},
            "dialogue_state": {"turn_index": 5},
            "scaffold_part": "\n",
        }
        s = assemble_brain_user_prompt(PromptAssemblyTier.FULL, p, collapse_level=0)
        self.assertNotIn("- persona:", s)
        self.assertNotIn("- psychology:", s)
        self.assertNotIn("- twin_profile:", s)
        self.assertNotIn("predictive_hint:", s)
        self.assertNotIn("style_hints:", s)
        self.assertNotIn("thinking_markers:", s)
        self.assertNotIn("typing_hooks:", s)
        self.assertNotIn("micro_emotion_style:", s)


if __name__ == "__main__":
    unittest.main()
