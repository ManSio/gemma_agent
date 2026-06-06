from core.llm_tiered import telemetry_kind_from_tag


def test_telemetry_kind_mapping():
    assert telemetry_kind_from_tag("brain_first") == "brain"
    assert telemetry_kind_from_tag("router_classifier:free_1") == "router_llm"
    assert telemetry_kind_from_tag("reflection_heavy") == "reflection_heavy"
    assert telemetry_kind_from_tag("mce_tick") == "mce"
    assert telemetry_kind_from_tag("brain_fast_chitchat") == "brain"
    assert telemetry_kind_from_tag("news_item_search") == "news_tools"
    assert telemetry_kind_from_tag("news_digest_llm") == "news_tools"
    assert telemetry_kind_from_tag("news_direct_rss") == "news_tools"
    assert telemetry_kind_from_tag("urlfetch") == "tools"
    assert telemetry_kind_from_tag("") == "chat"
