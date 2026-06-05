"""Тесты Knowledge Graph (без Qdrant — чистый flat-режим)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Dict

import pytest

from core.knowledge_graph import (
    KnowledgeGraphModule,
    _entity_id,
    _flat_load_all,
    _flat_path,
    _flat_save_all,
    _flat_search,
    _save_entity,
    _serialize,
    _text_for_embedding,
)

# ── Helpers ──


def _temp_kg_path():
    """Заменяем _flat_path() на временный файл для изоляции тестов."""
    return tempfile.mktemp(suffix=".jsonl")


def _reset_flat_path(tmp: str):
    """Переключаем _flat_path на временный файл."""
    import core.knowledge_graph as kg
    orig = kg._flat_path
    kg._flat_path = lambda: tmp
    return orig


def _cleanup(tmp: str):
    try:
        if os.path.isfile(tmp):
            os.unlink(tmp)
    except OSError:
        pass


# ── _entity_id ──


class TestEntityId:
    def test_stable_int(self):
        a = _entity_id("Алексей", "person")
        b = _entity_id("Алексей", "person")
        assert isinstance(a, int)
        assert a == b

    def test_different_types_give_different_ids(self):
        a = _entity_id("Минск", "city")
        b = _entity_id("Минск", "place")
        assert a != b

    def test_case_insensitive(self):
        a = _entity_id("Minsk", "city")
        b = _entity_id("minsk", "city")
        assert a == b

    def test_stripped(self):
        a = _entity_id("  Minsk  ", "city")
        b = _entity_id("minsk", "city")
        assert a == b


# ── _serialize ──


class TestSerialize:
    def test_minimal(self):
        s = _serialize("person", "Алексей")
        assert s["entity_type"] == "person"
        assert s["name"] == "Алексей"
        assert s["properties"] == {}
        assert s["relations"] == []
        assert "ts" in s

    def test_with_properties_and_relations(self):
        s = _serialize("person", "Алексей", {"age": 30}, [
            {"relation": "lives_in", "target_name": "Минск"},
        ])
        assert s["properties"]["age"] == 30
        assert len(s["relations"]) == 1


# ── _text_for_embedding ──


class TestTextForEmbedding:
    def test_basic(self):
        e = _serialize("person", "Алексей", {"city": "Минск"})
        t = _text_for_embedding(e)
        assert "person: Алексей" in t
        assert "city: Минск" in t

    def test_with_relations(self):
        e = _serialize("person", "Алексей", {}, [
            {"relation": "lives_in", "target_name": "Минск", "target_type": "city"},
        ])
        t = _text_for_embedding(e)
        assert "lives_in" in t
        assert "Минск" in t


# ── Flat persistence ──


class TestFlatPersistence:
    def test_save_and_load(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            eid = 12345
            rec = _serialize("person", "Тест", {"k": "v"})
            _flat_save_all({eid: rec})
            loaded = _flat_load_all()
            assert eid in loaded
            assert loaded[eid]["name"] == "Тест"
        finally:
            _cleanup(tmp)

    def test_empty(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            loaded = _flat_load_all()
            assert loaded == {}
        finally:
            _cleanup(tmp)

    def test_invalid_json_line_skipped(self):
        tmp = _temp_kg_path()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"eid": 1, "name": "ok"}) + "\n")
        _reset_flat_path(tmp)
        try:
            loaded = _flat_load_all()
            assert len(loaded) == 1
            assert loaded[1]["name"] == "ok"
        finally:
            _cleanup(tmp)


# ── Flat search ──


class TestFlatSearch:
    def test_search_finds_match(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            _flat_save_all({
                1: _serialize("person", "Алексей", {"city": "Минск"}),
                2: _serialize("city", "Москва"),
            })
            results = _flat_search("Алексей")
            assert len(results) >= 1
            assert results[0]["name"] == "Алексей"
        finally:
            _cleanup(tmp)

    def test_search_empty_returns_empty(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            results = _flat_search("nothing")
            assert results == []
        finally:
            _cleanup(tmp)

    def test_search_respects_limit(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            entries: Dict[int, Dict[str, Any]] = {}
            for i in range(5):
                entries[i] = _serialize("test", f"entity_{i}")
            _flat_save_all(entries)
            results = _flat_search("entity", limit=2)
            assert len(results) <= 2
        finally:
            _cleanup(tmp)

    def test_search_name_exact_boost(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            _flat_save_all({
                1: _serialize("person", "Иван", {"note": "Иван живёт в Минске"}),
                2: _serialize("person", "Пётр", {"note": "Пётр тоже Иван"}),
            })
            results = _flat_search("Иван")
            assert len(results) >= 1
            assert results[0]["name"] == "Иван"
        finally:
            _cleanup(tmp)


# ── _save_entity ──


class TestSaveEntity:
    def test_save_and_search(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            result = asyncio.run(_save_entity("person", "Алексей", {"city": "Минск"}))
            assert result["ok"] is True
            assert result["entity_type"] == "person"
            assert result["name"] == "Алексей"

            loaded = _flat_load_all()
            assert len(loaded) == 1
            eid = next(iter(loaded))
            assert loaded[eid]["name"] == "Алексей"
        finally:
            _cleanup(tmp)

    def test_save_overwrites_same(self):
        tmp = _temp_kg_path()
        _reset_flat_path(tmp)
        try:
            asyncio.run(_save_entity("person", "Алексей", {"city": "Минск"}))
            asyncio.run(_save_entity("person", "Алексей", {"city": "Москва"}))
            loaded = _flat_load_all()
            assert len(loaded) == 1  # перезапись, не дубль
        finally:
            _cleanup(tmp)


# ── KnowledgeGraphModule ──


class TestKnowledgeGraphModule:
    def setup_method(self):
        self.tmp = _temp_kg_path()
        _reset_flat_path(self.tmp)
        self.mod = KnowledgeGraphModule()

    def teardown_method(self):
        _cleanup(self.tmp)

    # entity_save

    def test_entity_save_minimal(self):
        result = asyncio.run(self.mod.entity_save("person", "Тест"))
        assert result["ok"] is True
        assert result["mode"] == "flat"

    def test_entity_save_with_properties(self):
        result = asyncio.run(self.mod.entity_save("place", "Минск", '{"country":"Беларусь"}'))
        assert result["ok"] is True
        loaded = _flat_load_all()
        found = [e for e in loaded.values() if e.get("name") == "Минск"]
        assert len(found) == 1
        assert found[0]["properties"]["country"] == "Беларусь"

    def test_entity_save_invalid_properties(self):
        result = asyncio.run(self.mod.entity_save("concept", "test", "not json"))
        assert result["ok"] is True
        loaded = _flat_load_all()
        found = [e for e in loaded.values() if e.get("name") == "test"]
        assert "_raw" in found[0]["properties"]

    def test_entity_save_validation_empty_type(self):
        result = asyncio.run(self.mod.entity_save("", "test"))
        assert result["ok"] is False

    def test_entity_save_validation_empty_name(self):
        result = asyncio.run(self.mod.entity_save("person", ""))
        assert result["ok"] is False

    def test_entity_save_validation_long_type(self):
        result = asyncio.run(self.mod.entity_save("a" * 41, "test"))
        assert result["ok"] is False

    # entity_relate

    def test_entity_relate_to_new(self):
        asyncio.run(self.mod.entity_save("person", "Алексей"))
        result = asyncio.run(self.mod.entity_relate("Алексей", "Минск", "lives_in", "city"))
        assert result["ok"] is True
        loaded = _flat_load_all()
        entry = [e for e in loaded.values() if e.get("name") == "Алексей"][0]
        assert len(entry["relations"]) == 1
        assert entry["relations"][0]["relation"] == "lives_in"
        assert entry["relations"][0]["target_name"] == "Минск"

    def test_entity_relate_without_prior_save(self):
        result = asyncio.run(self.mod.entity_relate("Новый", "Город", "located_in"))
        assert result["ok"] is True
        loaded = _flat_load_all()
        assert len(loaded) >= 1

    def test_entity_relate_validation(self):
        result = asyncio.run(self.mod.entity_relate("", "target"))
        assert result["ok"] is False

    # entity_search

    def test_entity_search(self):
        asyncio.run(self.mod.entity_save("person", "Алексей", '{"city":"Минск"}'))
        result = asyncio.run(self.mod.entity_search("Алексей"))
        assert result["ok"] is True
        assert result["count"] >= 1

    def test_entity_search_empty(self):
        result = asyncio.run(self.mod.entity_search(""))
        assert result["ok"] is False

    def test_entity_search_nothing(self):
        result = asyncio.run(self.mod.entity_search("xyznonexistent123"))
        assert result["ok"] is True
        assert result["count"] == 0

    # entity_delete

    def test_entity_delete(self):
        asyncio.run(self.mod.entity_save("person", "ToDelete"))
        result = asyncio.run(self.mod.entity_delete("ToDelete"))
        assert result["ok"] is True

    def test_entity_delete_not_found(self):
        result = asyncio.run(self.mod.entity_delete("NonExistent"))
        assert result["ok"] is False

    def test_entity_delete_empty(self):
        result = asyncio.run(self.mod.entity_delete(""))
        assert result["ok"] is False