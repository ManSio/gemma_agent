"""Атрибуция экземпляра (GEMMA_INSTANCE_* → brain_instance_attribution_block, identity.instance_author)."""

import os

import pytest

from core.brain import constants as C


def test_brain_instance_attribution_default_author(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMMA_INSTANCE_AUTHOR", raising=False)
    monkeypatch.delenv("GEMMA_INSTANCE_CREDIT_LINE", raising=False)
    monkeypatch.delenv("GEMMA_INSTANCE_ATTRIBUTION_ENABLED", raising=False)
    block = C.brain_instance_attribution_block()
    assert "GemmaProject" in block
    assert "Атрибуция экземпляра" in block


def test_brain_instance_attribution_custom_author(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMMA_INSTANCE_AUTHOR", "ACME")
    monkeypatch.delenv("GEMMA_INSTANCE_CREDIT_LINE", raising=False)
    block = C.brain_instance_attribution_block()
    assert "ACME" in block
    assert "GemmaProject" not in block


def test_brain_instance_attribution_custom_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMMA_INSTANCE_CREDIT_LINE", "Кредит одной строкой.")
    block = C.brain_instance_attribution_block()
    assert "Кредит одной строкой." in block


def test_brain_instance_attribution_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMMA_INSTANCE_ATTRIBUTION_ENABLED", "0")
    assert C.brain_instance_attribution_block() == ""


def test_gemma_instance_author_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMMA_INSTANCE_AUTHOR", raising=False)
    assert C.gemma_instance_author() == "GemmaProject"
    monkeypatch.setenv("GEMMA_INSTANCE_AUTHOR", "  X  ")
    assert C.gemma_instance_author() == "X"


def test_default_self_model_identity_has_instance_author(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import self_model as sm

    monkeypatch.setenv("GEMMA_INSTANCE_AUTHOR", "FromEnv")
    m = sm.default_self_model()
    ident = m.get("identity")
    assert isinstance(ident, dict)
    assert ident.get("instance_author") == "FromEnv"
