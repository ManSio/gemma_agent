"""Tests for core/news_consistency_checker.py."""

import pytest
from core.news_consistency_checker import NewsConsistencyChecker


@pytest.fixture
def checker():
    return NewsConsistencyChecker()


@pytest.mark.asyncio
async def test_check_dialogue_consistency_consistent(checker):
    recent = [{"user": "q", "bot": "Earthquake in Turkey.", "index": 0}]
    r = await checker.check_dialogue_consistency("u1", recent, "State of emergency in Turkey.")
    assert r["consistent"]
    assert r["conflicts"] == []
    assert r["recommendation"] == "safe"


@pytest.mark.asyncio
async def test_check_dialogue_consistency_date_conflict(checker):
    # Must use longer text (>60 chars) with common location and different dates
    prev_text = "Событие 15 марта 2023 года в Москве. Было объявлено чрезвычайное положение в центральном районе."
    new_text = "В Москве сейчас чрезвычайное положение 15 марта 2024 года. Ситуация отличается от прошлогодней."
    recent = [{"user": "q", "bot": prev_text, "index": 0}]
    r = await checker.check_dialogue_consistency("u2", recent, new_text)
    assert not r["consistent"]
    assert len(r["conflicts"]) >= 1
    assert r["recommendation"] in ("warn_user", "needs_fix")


def test_extract_entities_dates(checker):
    text = "25 \u044f\u043d\u0432\u0430\u0440\u044f 2024 \u0433\u043e\u0434\u0430. 01.02.2024. \u0412\u0447\u0435\u0440\u0430."
    e = checker.extract_entities(text)
    assert len(e["dates"]) >= 3


def test_extract_entities_people(checker):
    text = "\u0412\u043b\u0430\u0434\u0438\u043c\u0438\u0440 \u041f\u0443\u0442\u0438\u043d \u0432\u0441\u0442\u0440\u0435\u0442\u0438\u043b\u0441\u044f \u0441 \u0410\u043d\u0442\u043e\u043d\u043e\u043c \u0418\u0432\u0430\u043d\u043e\u0432\u044b\u043c."
    e = checker.extract_entities(text)
    assert len(e["people"]) >= 2


def test_extract_entities_empty(checker):
    assert checker.extract_entities("") == {"dates": [], "people": [], "locations": []}
    assert checker.extract_entities(None) == {"dates": [], "people": [], "locations": []}


@pytest.mark.asyncio
async def test_check_short_reply(checker):
    r = await checker.check_dialogue_consistency("u3", [], "\u0414\u0430.")
    assert r["consistent"]
    assert r["recommendation"] == "safe"


def test_safe_result(checker):
    r = checker._safe_result()
    assert r == {"consistent": True, "conflicts": [], "recommendation": "safe"}
