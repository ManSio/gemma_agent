"""Tests for core/agent_self_tools.py — Self-management tools."""

from core.agent_self_tools import AgentSelfToolsModule, _compact_value, _compact_config, _deep_get, _trim


# ── helpers ──


def test_deep_get():
    d = {"a": {"b": {"c": 42}}}
    assert _deep_get(d, "a.b.c") == 42
    assert _deep_get(d, "a.b") == {"c": 42}
    assert _deep_get(d, "x") is None
    assert _deep_get(d, "a.x") is None


def test_compact_value_passthrough():
    assert _compact_value(42) == 42
    assert _compact_value("hello") == "hello"
    assert _compact_value(True) is True
    assert _compact_value(None) is None


def test_compact_value_long_string():
    long = "x" * 500
    result = _compact_value(long)
    assert isinstance(result, str)
    assert len(result) == 200 + 3  # 200 chars + "..."
    assert result.endswith("...")


def test_compact_value_long_list():
    big = list(range(100))
    result = _compact_value(big)
    assert isinstance(result, dict)
    assert result["_count"] == 100
    assert len(result["_first"]) == 3
    assert result["_hint"] == "truncated"


def test_compact_config():
    big = {f"k{i}": i for i in range(60)}
    result = _compact_config(big, max_items=10)
    assert len(result) <= 11  # 10 keys + maybe _omitted
    assert "_omitted" in result


def test_trim_within_limit():
    d = {"a": "short"}
    assert _trim(d) == d


def test_trim_exceeds_limit():
    big_val = "x" * 100_000
    d = {"a": big_val}
    result = _trim(d)
    assert result.get("_truncated") is True


# ── AgentSelfToolsModule ──


def test_self_config_get_default():
    mod = AgentSelfToolsModule()
    result = asyncio_run(mod.self_config_get())
    assert isinstance(result, dict)
    assert result.get("ok") is True


def test_self_config_get_with_key():
    mod = AgentSelfToolsModule()
    result = asyncio_run(mod.self_config_get(key="config_version"))
    assert isinstance(result, dict)
    assert result.get("ok") is True


def test_self_config_get_bad_key():
    mod = AgentSelfToolsModule()
    result = asyncio_run(mod.self_config_get(key="nonexistent.key"))
    assert result.get("ok") is False
    assert "not found" in str(result.get("error", ""))


def test_self_metrics_default():
    mod = AgentSelfToolsModule()
    result = asyncio_run(mod.self_metrics())
    assert isinstance(result, dict)
    assert result.get("ok") is True
    assert "snapshot" in result
    assert "history_snapshots" in result


def test_self_status():
    mod = AgentSelfToolsModule()
    result = asyncio_run(mod.self_status())
    assert isinstance(result, dict)
    assert result.get("ok") is True
    assert "version" in result
    assert "uptime_sec" in result
    assert "memory_rss_bytes" in result
    assert "top_counters" in result


def test_self_status_has_brain_version():
    mod = AgentSelfToolsModule()
    result = asyncio_run(mod.self_status())
    v = result.get("version", {})
    assert "brain" in v
    assert v.get("compactor") == "1.0.0"


def test_module_is_auto_discoverable():
    """AgentSelfToolsModule ends with 'Module' — tools.py will auto-discover it."""
    import inspect
    from core.agent_self_tools import AgentSelfToolsModule as Cls

    assert Cls.__name__.endswith("Module")
    assert hasattr(Cls, "BRAIN_LITE_INCLUDE")
    assert Cls.BRAIN_LITE_INCLUDE is True

    # Check candidate methods are callable
    instance = Cls()
    for name in ("self_config_get", "self_metrics", "self_status"):
        assert hasattr(instance, name), f"missing {name}"
        assert callable(getattr(instance, name)), f"{name} not callable"


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
