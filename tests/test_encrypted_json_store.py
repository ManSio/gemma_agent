import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from core.encrypted_json_store import (
    encryption_enabled,
    migrate_plain_to_encrypted,
    read_encrypted_json,
    write_encrypted_json,
)


def test_plain_roundtrip_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MEM0_ENCRYPTION_KEY", raising=False)
    p = tmp_path / "facts.json"
    assert write_encrypted_json(p, [{"fact": "hello"}])
    data = read_encrypted_json(p, [])
    assert data == [{"fact": "hello"}]
    assert encryption_enabled() is False


def test_encrypted_roundtrip_with_key(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    p = tmp_path / "mem0_stub_store.json"
    payload = {"u1": [{"memory": "likes tea"}]}
    assert write_encrypted_json(p, payload)
    head = p.read_text(encoding="utf-8")[:10]
    assert head.startswith("GEMMAENC1:")
    assert read_encrypted_json(p, {}) == payload


def test_migrate_plain_to_encrypted(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    p = tmp_path / "store.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert migrate_plain_to_encrypted(p)
    assert p.read_text(encoding="utf-8").startswith("GEMMAENC1:")
    assert read_encrypted_json(p, {}) == {"a": 1}
