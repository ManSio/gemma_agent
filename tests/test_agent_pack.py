import pytest

from core.brain.agent import agent_instruction_effective
from core.brain.agent_pack import build_agent_instruction_for_turn
from core.brain.constants import AGENT_INSTRUCTION, AGENT_INSTRUCTION_COLLAPSE_STUB


def _minimal_tools():
    return {"UrlFetch.fetch_page": "x", "ArithmeticTool.evaluate": "x"}


def test_adaptive_off_matches_legacy_effective(monkeypatch):
    monkeypatch.setenv("BRAIN_AGENT_PACK_ADAPTIVE", "false")
    tools = _minimal_tools()
    s, meta = build_agent_instruction_for_turn(
        tools_mode="auto",
        tools_info=tools,
        user_text="привет",
        context={},
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
    )
    assert meta["pack"] == "full"
    assert s == agent_instruction_effective("auto", tools)


def test_chat_pack_shorter_than_full(monkeypatch):
    monkeypatch.setenv("BRAIN_AGENT_PACK_ADAPTIVE", "true")
    tools = _minimal_tools()
    s, meta = build_agent_instruction_for_turn(
        tools_mode="auto",
        tools_info=tools,
        user_text="привет, как дела?",
        context={},
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
    )
    assert meta["pack"] == "chat"
    assert len(s) < len(AGENT_INSTRUCTION) // 2
    assert "TOOL_CALL" in s


def test_url_forces_full(monkeypatch):
    monkeypatch.setenv("BRAIN_AGENT_PACK_ADAPTIVE", "true")
    s, meta = build_agent_instruction_for_turn(
        tools_mode="auto",
        tools_info=_minimal_tools(),
        user_text="открой https://example.com",
        context={},
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
    )
    assert meta["pack"] == "full"


def test_self_programming_forces_full(monkeypatch):
    monkeypatch.setenv("BRAIN_AGENT_PACK_ADAPTIVE", "true")
    tools = {**_minimal_tools(), "SelfProgramming.generate_module": "x"}
    s, meta = build_agent_instruction_for_turn(
        tools_mode="auto",
        tools_info=tools,
        user_text="сделай плагин echo",
        context={},
        task_tier="shallow",
        urls_chron=[],
        missing_facts=[],
        skill_name=None,
        skill_output={},
        image_intent=None,
    )
    assert meta["pack"] == "full"
    assert "SelfProgramming" in s or "платформ" in s.lower()


def test_law_insert_when_signal(monkeypatch):
    """Домен law — в prompt_modules (динамический хвост), не в agent_pack."""
    from core.brain.prompt_modules import build_dynamic_tail

    parts = {
        "user_text": "что говорит закон о сроке?",
        "tool_names": ["UniversalSearch.search", "DocumentCorpus.unified_search"],
    }
    tail = build_dynamic_tail(parts, "standard", "general", parts)
    assert "UniversalSearch" in tail or "DocumentCorpus" in tail
    assert "domain_law" in tail


def test_collapse_stub_replaces_agent_in_full_prompt():
    from core.brain.prompt_pack import assemble_brain_user_prompt
    from core.prompt_assembly import PromptAssemblyTier

    agent_long = "VERY_LONG_AGENT" * 200
    p = {
        "system_prompt_for_llm": "sys",
        "agent_inst": agent_long,
        "agent_inst_collapse_stub": AGENT_INSTRUCTION_COLLAPSE_STUB,
        "intent_addon": "",
        "user_text": "hi",
        "user_id": "1",
        "memory_facts": [],
        "recent_dialogue": [],
        "dialogue_summary": "",
        "grounding_mini": {},
        "document_intake_block": "",
        "user_facts": {},
        "routing_prefs_hint": "",
        "tcmd_cat": "",
        "plugin_manifest_prompts": "",
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
        "tool_names_full_index": "",
        "urls_in_message": [],
        "group_chat_addon": "",
        "persona": {},
        "psychology": {},
        "twin_profile": {},
        "topic_tracking": {},
        "group_context": {},
        "user_facts_meta": {},
        "missing_facts": [],
        "auto_ask_hint": "",
        "behavior_policy": {},
        "predictive_hint": {},
        "goal_hints": {},
        "blended_stable": {},
        "goal_plan": {},
        "style_hints": {},
        "micro_emotion_style": {},
        "dialogue_state": {},
        "thinking_markers": {},
        "typing_hooks": {},
        "scaffold_part": "\n",
        "tool_routing_hint": "",
        "telegram_reply_block": "",
    }
    from core.brain.prompt_pack import _build_dynamic_tail_legacy, _labels, _full_limits

    low = _build_dynamic_tail_legacy(p, _labels(), _full_limits(0), PromptAssemblyTier.FULL, 0)
    high = _build_dynamic_tail_legacy(p, _labels(), _full_limits(2), PromptAssemblyTier.FULL, 2)
    assert "VERY_LONG_AGENT" in low
    assert "VERY_LONG_AGENT" not in high
    assert "TOOL_CALL" in high or "агент" in high.lower()
