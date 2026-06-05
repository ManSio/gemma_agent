"""Tests for GroupBehaviorModule."""
import json
import tempfile
import pytest

from modules.group_behavior.module import GroupBehaviorModule


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def module(temp_storage):
    return GroupBehaviorModule(config={"storage_path": temp_storage})


def test_handle_message(module):
    result = module.handle_group_message("school_group_1", "Помогите, не могу решить задачу")
    assert result["group_id"] == "school_group_1"
    assert result["should_intervene"] is True


def test_handle_message_no_intervention(module):
    result = module.handle_group_message("normal_chat", "Всем привет!")
    assert result["should_intervene"] is False


def test_group_type_detection(module):
    r1 = module.handle_group_message("school_class_5a", "тест")
    assert r1["group_type"] == "school_group"

    r2 = module.handle_group_message("parent_chat", "тест")
    assert r2["group_type"] == "parent_group"

    r3 = module.handle_group_message("study_buddy", "тест")
    assert r3["group_type"] == "study_group"

    r4 = module.handle_group_message("random", "тест")
    assert r4["group_type"] == "normal_group"


def test_generate_reply(module):
    reply = module.generate_group_reply({"group_type": "school_group", "message": "Вопрос по алгебре"})
    assert isinstance(reply, str)
    assert reply.startswith("[шаблон]")


def test_generate_reply_long_message(module):
    """Long message should prepend a random element."""
    long_msg = "A" * 150
    reply = module.generate_group_reply({"group_type": "school_group", "message": long_msg})
    assert isinstance(reply, str)
    assert len(reply) > 0


def test_should_intervene(module):
    assert module.should_intervene("normal_group", "Помогите, я не могу!") is True
    assert module.should_intervene("normal_group", "просто привет") is False


def test_group_stats_persistence(module):
    module.handle_group_message("group1", "Помогите!")
    module.handle_group_message("group1", "ещё вопрос")
    stats = module._group_stats("group1")
    assert stats["messages_count"] == 2
    assert stats["interventions"] == 1


def test_get_group_behavior_orchestrator_contract(module):
    module.handle_group_message("school_group_1", "Помогите с задачей")
    live = module.get_group_behavior("school_group_1")
    assert live.get("group_id") == "school_group_1"
    assert live.get("group_type") == "school_group"
    assert live.get("messages_count", 0) >= 1
    assert live.get("reply_source") == "brain"
    assert "brain" in (live.get("hint") or "").lower()
    assert isinstance(live.get("template_sample"), str)


def test_get_group_behavior_empty_group(module):
    live = module.get_group_behavior("-100999")
    assert live.get("group_id") == "-100999"
    assert live.get("messages_count") == 0


def test_persistence_across_instances(temp_storage):
    m1 = GroupBehaviorModule(config={"storage_path": temp_storage})
    m1.handle_group_message("g1", "тест")

    m2 = GroupBehaviorModule(config={"storage_path": temp_storage})
    stats = m2._group_stats("g1")
    assert stats["messages_count"] == 1


def test_execute_handle(module):
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/handle_group_message grp тест"}}))
    assert len(outputs) == 1


def test_execute_stats(module):
    import asyncio
    module.handle_group_message("g1", "тест")
    outputs = asyncio.run(module.execute({"input": {"payload": "/group_stats g1"}}))
    assert len(outputs) == 1
    assert "Статистика" in outputs[0].payload


def test_execute_unknown_command(module):
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/unknown"}}))
    assert len(outputs) == 1
    assert "Команды:" in outputs[0].payload
