"""Encrypted JSON persistence for local memory stores (Mem0 stub, facts.json)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Union

from cryptography.fernet import Fernet, InvalidToken

from core.json_atomic import atomic_write_json

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]
_ENC_PREFIX = "GEMMAENC1:"
_LEGACY_PLAIN_OK = True


def _normalize_key(raw: str) -> str:
    s = (raw or "").replace("\ufeff", "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    return s


def encryption_enabled() -> bool:
    raw = (os.getenv("ENCRYPTION_KEY") or os.getenv("MEM0_ENCRYPTION_KEY") or "").strip()
    return len(raw) >= 8


def _fernet() -> Optional[Fernet]:
    raw = _normalize_key(os.getenv("ENCRYPTION_KEY") or os.getenv("MEM0_ENCRYPTION_KEY") or "")
    if len(raw) < 8:
        return None
    try:
        return Fernet(raw.encode("utf-8"))
    except ValueError:
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        return Fernet(derived)


def _encrypt_payload(data: Any) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError("encryption key missing")
    blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return _ENC_PREFIX + f.encrypt(blob).decode("ascii")


def _decrypt_payload(text: str) -> Any:
    f = _fernet()
    if f is None:
        raise RuntimeError("encryption key missing")
    token = text[len(_ENC_PREFIX) :].encode("ascii")
    plain = f.decrypt(token).decode("utf-8")
    return json.loads(plain)


def read_encrypted_json(path: PathLike, default: Any) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError:
        logger.exception("[encrypted_json_store] read failed path=%s", p)
        return default
    if raw_text.startswith(_ENC_PREFIX):
        if not encryption_enabled():
            logger.warning("[encrypted_json_store] encrypted file but no ENCRYPTION_KEY path=%s", p)
            return default
        try:
            return _decrypt_payload(raw_text)
        except (InvalidToken, json.JSONDecodeError, ValueError):
            logger.exception("[encrypted_json_store] decrypt failed path=%s", p)
            return default
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        logger.exception("[encrypted_json_store] invalid plain json path=%s", p)
        return default


def write_encrypted_json(path: PathLike, data: Any, *, indent: Optional[int] = 2) -> bool:
    p = Path(path)
    if encryption_enabled():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = _encrypt_payload(data)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(p)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            return True
        except Exception:
            logger.exception("[encrypted_json_store] encrypt write failed path=%s", p)
            return False
    if not _LEGACY_PLAIN_OK:
        logger.error("[encrypted_json_store] ENCRYPTION_KEY required for write path=%s", p)
        return False
    return atomic_write_json(p, data, indent=indent)


def migrate_plain_to_encrypted(path: PathLike) -> bool:
    """One-shot migration: plain JSON → encrypted when key is set."""
    p = Path(path)
    if not p.is_file() or not encryption_enabled():
        return False
    try:
        head = p.read_text(encoding="utf-8")[:16]
    except OSError:
        return False
    if head.startswith(_ENC_PREFIX):
        return True
    data = read_encrypted_json(p, None)
    if data is None:
        return False
    return write_encrypted_json(p, data)
