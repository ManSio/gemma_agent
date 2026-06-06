from core.document_intake import format_document_intake_for_brain
from core.brain.prompt_pack import assemble_brain_user_prompt
from core.prompt_assembly import PromptAssemblyTier


def test_format_document_intake_success():
    s = format_document_intake_for_brain({"ok": True, "text": "Hello PDF"}, max_chars=1000)
    assert "Hello PDF" in s
    assert "[вложение]" in s


def test_format_document_intake_empty_layer():
    s = format_document_intake_for_brain({"ok": True, "text": "", "text_layer_empty": True})
    assert "скан" in s.lower() or "текстового слоя" in s.lower()


def test_format_document_intake_error():
    s = format_document_intake_for_brain({"ok": False, "error": "boom"})
    assert "boom" in s


def test_format_document_intake_worker_timeout_hint():
    s = format_document_intake_for_brain({"ok": False, "error": "worker_timeout_or_failed"})
    assert "worker_timeout_or_failed" in s
    assert "HEAVY_WORKER_TIMEOUT_SEC" in s


def test_prompt_pack_includes_document_block():
    parts = {
        "system_prompt_for_llm": "sys",
        "agent_inst": "agent",
        "intent_addon": "",
        "user_text": "doc silent",
        "user_id": "u1",
        "memory_facts": [],
        "recent_dialogue": [],
        "dialogue_summary": "",
        "grounding_mini": "g",
        "document_intake_block": format_document_intake_for_brain({"ok": True, "text": "BODY"}, max_chars=500),
        "user_facts": {},
        "routing_prefs_hint": "",
        "tcmd_cat": "",
        "sess_first": "",
        "pteacher": "",
        "operator_rules": "",
        "ephemeral_lessons": "",
        "task_facts": {},
        "knowledge_summary": "",
        "external_hint": "",
        "tool_routing_hint": "",
        "vp_ctx": "",
        "skill_name": "",
        "image_intent": "",
        "skill_output": {},
        "skill_hint": "",
        "ocr_text": "",
        "tools_mode": "lite",
        "tool_names": [],
        "urls_in_message": [],
        "group_chat_addon": "",
    }
    out = assemble_brain_user_prompt(PromptAssemblyTier.IMAGE_SLIM, parts)
    assert "BODY" in out
    assert "document_intake" in out
