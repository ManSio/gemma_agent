from core.route_semantic_audit import (
    build_semantic_audit_note,
    route_semantic_audit_live_override_enabled,
)


def test_mismatch_note_without_override():
    note = build_semantic_audit_note(
        user_text="новости",
        final_profile="news_brief",
        classifier_profile="standard",
        classifier_confidence=0.42,
        router_source="llm",
    )
    assert note and note.get("mismatch") is True
    assert note.get("final_profile") == "news_brief"
    assert route_semantic_audit_live_override_enabled() is False


def test_no_note_when_profiles_match():
    assert (
        build_semantic_audit_note(
            user_text="привет",
            final_profile="short",
            classifier_profile="short",
        )
        is None
    )
