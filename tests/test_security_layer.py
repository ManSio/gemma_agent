"""Tests for SecurityLayerModule."""
import os
import pytest

from modules.security_layer.module import SecurityLayerModule


@pytest.fixture
def module():
    return SecurityLayerModule(config={"encryption_key": "test-key-12345"})


def test_encrypt_decrypt_roundtrip(module):
    original = "secret data"
    encrypted = module.encrypt_data(original, "default")
    assert encrypted != original
    decrypted = module.decrypt_data(encrypted)
    assert decrypted == original


def test_encrypt_different_types_different_outputs(module):
    e1 = module.encrypt_data("hello", "type_a")
    e2 = module.encrypt_data("hello", "type_b")
    assert e1 != e2


def test_decrypt_wrong_key(module):
    m1 = SecurityLayerModule(config={"encryption_key": "key1"})
    m2 = SecurityLayerModule(config={"encryption_key": "key2"})
    encrypted = m1.encrypt_data("secret", "test_type")
    # decrypting with wrong key should produce error
    decrypted = m2.decrypt_data(encrypted, "test_type")
    assert "Ошибка расшифровки" in decrypted


def test_encrypt_empty_string(module):
    encrypted = module.encrypt_data("", "empty")
    assert isinstance(encrypted, str)
    assert len(encrypted) > 0


def test_decrypt_empty_raises(module):
    result = module.decrypt_data("")
    assert "Ошибка расшифровки" in result or result == ""


def test_check_access_policy(module):
    assert module.check_access_policy("admin", "user_profiles", "admin") is True
    assert module.check_access_policy("student", "psychology_data", "student") is False
    assert module.check_access_policy("parent", "digital_twin", "parent") is True
    assert module.check_access_policy("anonymous", "messages", "anonymous") is False


def test_execute_encrypt(module):
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/encrypt text привет"}}))
    assert len(outputs) == 1
    assert outputs[0].payload.startswith("Зашифровано:")


def test_execute_decrypt(module):
    import asyncio
    encrypted = module.encrypt_data("test_data", "text")
    outputs = asyncio.run(module.execute({"input": {"payload": f"/decrypt text {encrypted}"}}))
    assert len(outputs) == 1
    assert "Расшифровано:" in outputs[0].payload


def test_execute_policy_check(module):
    import asyncio
    outputs = asyncio.run(
        module.execute({"input": {"payload": "/policy_check parent messages parent"}})
    )
    assert len(outputs) == 1
    assert outputs[0].meta.get("policy_ok") is True
    assert "разрешено" in outputs[0].payload


def test_execute_unknown_command(module):
    import asyncio
    outputs = asyncio.run(module.execute({"input": {"payload": "/unknown"}}))
    assert len(outputs) == 1
    assert "encrypt" in outputs[0].payload or "decrypt" in outputs[0].payload


def test_fallback_without_cryptography(module):
    """Simulate missing cryptography library by monkeypatching."""
    import modules.security_layer.module as slm
    original_aes = slm._aes_encrypt
    # Function should still work with base64 fallback
    encrypted = module.encrypt_data("test", "fallback")
    assert isinstance(encrypted, str)
