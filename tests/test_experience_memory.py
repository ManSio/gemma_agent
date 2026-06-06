"""Tests for core/experience_memory.py — experience learning from outcomes."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.experience_memory import (
    append_experience_record,
    append_success,
    build_hint_for_context,
    classify_turn_outcome,
    find_hints,
    find_negative_hints,
    fingerprint,
    semantic_failure_reason,
)


def operational_diag_reply() -> str:
    return (
        "Проверить баланс OpenRouter или ваш API‑ключ из обычного чата я не могу — "
        "это не видно модели. Если вы администратор этого бота: "
        "/admin_connectivity (Telegram, OpenRouter, Mem0) и /admin_health (сводка, в т.ч. сбои внешних API)."
    )


# ── fingerprint ──


def test_fingerprint_basic():
    fp = fingerprint("привет как дела")
    assert fp
    assert len(fp) >= 6


def test_fingerprint_empty():
    assert fingerprint("") == ""
    assert fingerprint(None) == ""
    assert fingerprint("   ") == ""


def test_fingerprint_stable():
    a = fingerprint("сколько будет 2+2?")
    b = fingerprint("сколько будет 2+2?")
    assert a == b


# ── classify_turn_outcome ──


def test_classify_ok():
    outputs = [SimpleNamespace(type="text", payload="Вот ответ")]
    assert classify_turn_outcome(outputs, user_text="привет") == "ok"


def test_classify_chitchat_reciprocal_question_is_ok():
    """«Как дела» + ответ с «Как сам?» — не clarify."""
    outputs = [SimpleNamespace(type="text", payload="Привет! У меня всё отлично. Как сам?")]
    assert classify_turn_outcome(outputs, user_text="как дела") == "ok"


def test_classify_clarify_question():
    """Ответ с уточняющим вопросом (эвристика _CLARIFY_HINT_RE) → clarify."""
    payload = "Что именно вы имеете в виду — A или B?"
    outputs = [SimpleNamespace(type="text", payload=payload)]
    assert classify_turn_outcome(outputs, user_text="непонятно") == "clarify"

def test_classify_translation_question_is_ok_not_clarify():
    # Перевод фразы может заканчиваться вопросом ("How are you?"), но это не уточнение.
    outputs = [SimpleNamespace(type="text", payload="Hello, how are you?")]
    assert classify_turn_outcome(outputs, user_text="переведи на английский: привет, как дела") == "ok"


def test_classify_empty():
    assert classify_turn_outcome([], user_text="") == "failure"
    # error type with empty text -> failure (meta not set on SimpleNamespace)
    result = classify_turn_outcome([SimpleNamespace(type="error", payload="err")], user_text="")
    assert result == "failure"


# ── semantic_failure_reason ──


def test_semantic_failure_empty():
    # empty reply - no semantic failure reason
    result = semantic_failure_reason("вопрос", "")
    assert result in ("", None)


def test_semantic_failure_none():
    # good answer - no failure
    assert not semantic_failure_reason("вопрос", "понятный ответ")


# ── append_success ──


def test_append_success_operational_diag_filtered():
    p = os.path.join(tempfile.gettempdir(), "test_exp_opdiag.jsonl")
    try:
        os.remove(p)
    except OSError:
        pass
    with patch.dict(os.environ, {"GEMMA_EXPERIENCE_PATH": p, "EXPERIENCE_MEMORY_ENABLED": "true"}, clear=False):
        append_success(
            user_text="продолжи разбор дельты",
            intent="general",
            module="chat-orchestrator",
            planner_reason="intent_module_match",
            assistant_excerpt=operational_diag_reply(),
        )
        # operational diag replies are filtered out
        assert not os.path.exists(p) or os.path.getsize(p) == 0


def test_append_success_writes():
    p = os.path.join(tempfile.gettempdir(), "test_exp_success.jsonl")
    try:
        os.remove(p)
    except OSError:
        pass
    with patch.dict(os.environ, {"GEMMA_EXPERIENCE_PATH": p, "EXPERIENCE_MEMORY_ENABLED": "true"}, clear=False):
        append_success(
            user_text="сколько будет 2+2 в контексте бухгалтерии",
            intent="general",
            module="chat-orchestrator",
            planner_reason="intent_module_match",
            assistant_excerpt="В бухучёте 2+2 может быть 5 — шутка. Арифметически 4.",
        )
        assert os.path.exists(p)
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["intent"] == "general"
        assert record["outcome"] == "ok"
        assert "skill" in record  # skill_name field present


def test_append_success_with_skill():
    p = os.path.join(tempfile.gettempdir(), "test_exp_skill.jsonl")
    try:
        os.remove(p)
    except OSError:
        pass
    with patch.dict(os.environ, {"GEMMA_EXPERIENCE_PATH": p, "EXPERIENCE_MEMORY_ENABLED": "true"}, clear=False):
        append_success(
            user_text="переведи на английский",
            intent="general",
            module="chat-orchestrator",
            planner_reason="intent_module_match",
            assistant_excerpt="Here is the translation",
            skill_name="translator",
        )
        with open(p, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["skill"] == "translator"


# ── append_experience_record ──


def test_append_experience_clarify():
    p = os.path.join(tempfile.gettempdir(), "test_exp_clarify.jsonl")
    try:
        os.remove(p)
    except OSError:
        pass
    env = {
        "GEMMA_EXPERIENCE_PATH": p,
        "EXPERIENCE_MEMORY_ENABLED": "true",
        "EXPERIENCE_NEGATIVE_RECORDING_ENABLED": "true",
        "EXPERIENCE_NEGATIVE_HINT_ENABLED": "true",
        "EXPERIENCE_NEGATIVE_HINT_UNCERTAIN_ONLY": "false",
    }
    with patch.dict(os.environ, env, clear=False):
        append_experience_record(
            user_text="повтори расчёт дельты",
            intent="general",
            module="chat-orchestrator",
            planner_reason="match",
            outcome="clarify",
            assistant_excerpt="Уточните, какой расчёт",
        )
        assert os.path.exists(p)
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1


def test_append_experience_with_skill():
    p = os.path.join(tempfile.gettempdir(), "test_exp_neg_skill.jsonl")
    try:
        os.remove(p)
    except OSError:
        pass
    env = {
        "GEMMA_EXPERIENCE_PATH": p,
        "EXPERIENCE_MEMORY_ENABLED": "true",
        "EXPERIENCE_NEGATIVE_RECORDING_ENABLED": "true",
    }
    with patch.dict(os.environ, env, clear=False):
        append_experience_record(
            user_text="какую машину выбрать",
            intent="general",
            module="chat-orchestrator",
            planner_reason="match",
            outcome="fallback",
            assistant_excerpt="",
            skill_name="auto_vehicle",
        )
        with open(p, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["skill"] == "auto_vehicle"


# ── build_hint_for_context ──


class TestBuildHintForContext(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mktemp(suffix=".jsonl")
        self.env = {
            "GEMMA_EXPERIENCE_PATH": self.p,
            "EXPERIENCE_MEMORY_ENABLED": "true",
        }

    def tearDown(self):
        try:
            os.remove(self.p)
        except OSError:
            pass

    def _append_success(self, user_text, excerpt, intent="general", module="x", planner_reason="r"):
        with patch.dict(os.environ, self.env, clear=False):
            append_success(user_text=user_text, intent=intent, module=module, planner_reason=planner_reason, assistant_excerpt=excerpt)

    def test_build_hint_basic(self):
        self._append_success("тест опыта", "ответ про опыт")
        with patch.dict(os.environ, self.env, clear=False):
            from types import SimpleNamespace
            hint = build_hint_for_context(
                user_text="новый запрос",
                intent="general",
                module="chat-orchestrator",
                decision=SimpleNamespace(fallback=False, reason=""),
                predictive_hint=None,
            )
        # call must not raise, returns a string
        assert isinstance(hint, str)


# ── find_hints integration ──


class TestFindHints(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mktemp(suffix=".jsonl")
        self.env = {
            "GEMMA_EXPERIENCE_PATH": self.p,
            "EXPERIENCE_MEMORY_ENABLED": "true",
            "EXPERIENCE_NEGATIVE_HINT_ENABLED": "true",
            "EXPERIENCE_NEGATIVE_HINT_UNCERTAIN_ONLY": "false",
            "EXPERIENCE_NEGATIVE_RECORDING_ENABLED": "true",
        }

    def tearDown(self):
        try:
            os.remove(self.p)
        except OSError:
            pass

    def test_find_hints_positive(self):
        with patch.dict(os.environ, self.env, clear=False):
            append_success(
                user_text="список покупок",
                intent="general",
                module="chat-orchestrator",
                planner_reason="r",
                assistant_excerpt="Вот список покупок: молоко, хлеб, яйца",
            )
            hints = find_hints(user_text="список покупок", intent="general", module="chat-orchestrator", limit=3)
            assert hints is not None

    def test_find_negative_hints(self):
        with patch.dict(os.environ, self.env, clear=False):
            append_experience_record(
                user_text="ещё раз про риск",
                intent="general",
                module="chat-orchestrator",
                planner_reason="match",
                outcome="failure",
                assistant_excerpt="Не удалось оценить риск",
            )
            hints = find_negative_hints(user_text="про риск", intent="general", module="chat-orchestrator")
            assert hints is not None
