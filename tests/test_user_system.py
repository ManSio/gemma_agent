"""Tests for UserSystemModule (core implementation in modules/user_system)."""
import json
import os
import tempfile
import pytest

from modules.user_system.module import UserSystemModule


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def module(temp_storage):
    return UserSystemModule(config={"storage_path": temp_storage})


def test_get_user_not_found(module):
    assert module.get_user("nonexistent") == {}


def test_create_and_get_user(module):
    ok = module.update_user("user1", {"name": "Alice", "age": 30})
    assert ok
    data = module.get_user("user1")
    assert data["name"] == "Alice"
    assert data["user_id"] == "user1"
    assert "created_at" in data


def test_update_existing_user(module):
    module.update_user("user1", {"name": "Alice"})
    module.update_user("user1", {"age": 31})
    data = module.get_user("user1")
    assert data["name"] == "Alice"
    assert data["age"] == 31


def test_append_history(module):
    module.update_user("user1", {"name": "Alice"})
    ok = module.append_history("user1", {"role": "user", "text": "Hello"})
    assert ok
    data = module.get_user("user1")
    assert len(data["history"]) == 1
    assert data["history"][0]["message"]["text"] == "Hello"


def test_history_capped_at_50(module):
    module.update_user("user1", {"name": "Alice"})
    for i in range(60):
        module.append_history("user1", {"role": "user", "text": f"msg{i}"})
    data = module.get_user("user1")
    assert len(data["history"]) == 50
    assert data["history"][0]["message"]["text"] == "msg10"


def test_set_role(module):
    module.update_user("user1", {"name": "Alice"})
    ok = module.set_role("user1", "admin")
    assert ok
    data = module.get_user("user1")
    assert data["role"] == "admin"


def test_link_parent(module):
    ok = module.link_parent("child1", "parent1")
    assert ok
    child = module.get_user("child1")
    assert child["family"]["parents"] == ["parent1"]
    parent = module.get_user("parent1")
    assert parent["family"]["children"] == ["child1"]


def test_execute_get_user(module):
    module.update_user("user1", {"name": "Alice"})
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/get_user user1"}}))
    assert len(outputs) == 1
    assert "Alice" in outputs[0].payload


def test_execute_update_user(module):
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": '/update_user user1 {"name": "Bob"}'}}))
    assert len(outputs) == 1
    data = module.get_user("user1")
    assert data["name"] == "Bob"


def test_execute_unknown_command(module):
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/unknown"}}))
    assert len(outputs) == 1
    assert "Команды:" in outputs[0].payload


def test_persistence_across_instances(temp_storage):
    m1 = UserSystemModule(config={"storage_path": temp_storage})
    m1.update_user("user1", {"name": "Alice"})

    m2 = UserSystemModule(config={"storage_path": temp_storage})
    data = m2.get_user("user1")
    assert data["name"] == "Alice"


def test_error_handling_bad_json(module):
    """update_user with invalid JSON should not crash."""
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/update_user user1 {bad json}"}}))
    assert len(outputs) == 1
    assert "Ошибка" in outputs[0].payload
