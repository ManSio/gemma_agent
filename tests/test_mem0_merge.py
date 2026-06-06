"""Объединение результатов Mem0 из двух проектов."""
import os

from core.mem0_memory.mem0_module import (
    Mem0MemoryModule,
    _coerce_payload_for_self_hosted,
    _merge_search_payloads,
    _normalize_mem0_api_key,
    load_mem0_config_from_env,
)


def test_normalize_mem0_key_strips_prefix_and_quotes():
    assert _normalize_mem0_api_key('Token abcdef') == "abcdef"
    assert _normalize_mem0_api_key('Bearer xyz') == "xyz"
    assert _normalize_mem0_api_key('"m0-secret"') == "m0-secret"


def test_normalize_mem0_key_bom_newline_comment():
    assert _normalize_mem0_api_key("\ufeffm0-hello") == "m0-hello"
    assert _normalize_mem0_api_key("m0-first\nm0-second") == "m0-first"
    assert _normalize_mem0_api_key("m0-key# trailing comment") == "m0-key"


def test_merge_search_dedupes_by_memory_text():
    a = {
        "results": [
            {"memory": "User likes tea", "score": 0.9, "id": "1"},
            {"memory": "user likes tea", "score": 0.5, "id": "2"},
        ]
    }
    b = {
        "results": [
            {"memory": "User likes coffee", "score": 0.8, "id": "3"},
        ]
    }
    out = _merge_search_payloads(a, b, top_k=10)
    texts = {x["content"] for x in out}
    assert "User likes tea" in texts or "user likes tea" in texts
    assert "User likes coffee" in texts
    assert len(out) == 2


def test_merge_ignores_bad_inputs():
    assert _merge_search_payloads(None, {}, top_k=5) == []
    assert _merge_search_payloads({"results": []}, top_k=5) == []


def test_merge_self_hosted_text_field():
    resp = {
        "results": [
            {"text": "Кошка Мурка любит играть", "score": 0.8, "id": 1},
        ]
    }
    out = _merge_search_payloads(resp, top_k=5)
    assert len(out) == 1
    assert "Мурка" in out[0]["content"]


def test_coerce_self_hosted_add_and_search():
    base = "http://127.0.0.1:8001"
    add_in = {
        "user_id": "111",
        "messages": [{"role": "user", "content": "запомни: кошка Мурка"}],
    }
    add_out = _coerce_payload_for_self_hosted("/v3/memories/add/", add_in, base)
    assert "запомни" in add_out["text"]
    search_in = {"query": "кошка", "filters": {"user_id": "111"}, "top_k": 10}
    search_out = _coerce_payload_for_self_hosted("/v3/memories/search/", search_in, base)
    assert search_out["user_id"] == "111"
    assert search_out["limit"] == 10


def test_search_filters_or_user_ids(monkeypatch):
    monkeypatch.setenv("MEM0_SEARCH_OR_USER_IDS", "genesis-project, mem0-mcp-user")
    m = Mem0MemoryModule({"mem0_api_key": "test-key"})
    f = m._search_filters("111")
    assert f == {
        "OR": [
            {"user_id": "111"},
            {"user_id": "genesis-project"},
            {"user_id": "mem0-mcp-user"},
        ]
    }


def test_search_filters_single_user(monkeypatch):
    monkeypatch.delenv("MEM0_SEARCH_OR_USER_IDS", raising=False)
    m = Mem0MemoryModule({"mem0_api_key": "test-key"})
    assert m._search_filters("111") == {"user_id": "111"}


def test_key_label_primary_vs_mirror():
    m = Mem0MemoryModule(
        {
            "mem0_api_key": "primary-k",
            "mem0_mirror_api_key": "mirror-k",
            "mem0_mirror_api_url": "https://api.mem0.ai",
        }
    )
    assert m._key_label(None, None) == "primary"
    assert m._key_label("mirror-k", None) == "mirror"
    assert m._key_label(None, "https://other.example") == "mirror"


def test_load_mem0_config_local_without_cloud_key(monkeypatch):
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.setenv("MEM0_LOCAL", "true")
    monkeypatch.setenv("MEM0_API_URL", "http://127.0.0.1:8001")
    cfg = load_mem0_config_from_env()
    assert cfg is not None
    assert cfg["mem0_api_key"] == "local"
    assert cfg["mem0_api_url"] == "http://127.0.0.1:8001"


def test_load_mem0_config_local_custom_placeholder(monkeypatch):
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.setenv("MEM0_SELF_HOSTED", "1")
    monkeypatch.setenv("MEM0_API_URL", "http://mem0:8001")
    monkeypatch.setenv("MEM0_LOCAL_API_KEY", "my-dev-secret")
    cfg = load_mem0_config_from_env()
    assert cfg["mem0_api_key"] == "my-dev-secret"
    assert cfg["mem0_api_url"] == "http://mem0:8001"


def test_load_mem0_config_no_key_no_local_returns_none(monkeypatch):
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_LOCAL", raising=False)
    monkeypatch.delenv("MEM0_SELF_HOSTED", raising=False)
    monkeypatch.setenv("MEM0_API_URL", "http://127.0.0.1:8001")
    assert load_mem0_config_from_env() is None


def test_disable_mirror_write_runtime(monkeypatch):
    monkeypatch.setenv("MEM0_MIRROR_WRITE", "true")
    m = Mem0MemoryModule(
        {"mem0_api_key": "a", "mem0_mirror_api_key": "b"},
    )
    assert m._mirror_write is True
    m.disable_mirror_write_runtime("unit test")
    assert m._mirror_write is False
    m.disable_mirror_write_runtime("again")
    assert m._mirror_write is False


def test_local_simple_retry_target_search():
    m = Mem0MemoryModule({"mem0_api_key": "test-key"})
    path, payload = m._local_simple_retry_target("/v3/memories/search/", {"query": "hello"})
    assert path == "/search"
    assert payload == {"query": "hello"}


def test_local_simple_retry_target_add_from_messages():
    m = Mem0MemoryModule({"mem0_api_key": "test-key"})
    path, payload = m._local_simple_retry_target(
        "/v3/memories/add/",
        {"messages": [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]},
    )
    assert path == "/add"
    assert payload["text"] == "u\na"
