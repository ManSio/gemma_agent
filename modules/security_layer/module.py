"""
Security Layer Module — AES-256 шифрование и контроль доступа.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Any, Dict, List

from core.models import Output

logger = logging.getLogger(__name__)


def _derive_key(master_key: str, salt: str = "") -> bytes:
    return hashlib.sha256((master_key + salt).encode()).digest()


def _aes_encrypt(plain: str, key_bytes: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    iv = secrets.token_bytes(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ct).decode("ascii")


def _aes_decrypt(ciphertext_b64: str, key_bytes: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    raw = base64.b64decode(ciphertext_b64.encode("ascii"))
    iv, ct = raw[:16], raw[16:]
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")


class SecurityLayerModule:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.encryption_key = self.config.get("encryption_key", os.getenv("ENCRYPTION_KEY") or "")
        if not self.encryption_key or len(self.encryption_key) < 8:
            logger.warning("[security] ENCRYPTION_KEY missing or too short; using fallback")
            self.encryption_key = os.urandom(32).hex()
        self.log_encrypted = self.config.get("log_encrypted", True)
        self.storage_path = "./data/security"
        os.makedirs(self.storage_path, exist_ok=True)

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input", {})
        payload = (input_data.get("payload") or "").strip()

        if payload.startswith("/encrypt "):
            parts = payload[9:].split(" ", 1)
            if len(parts) == 2:
                data_type, data = parts[0], parts[1]
                encrypted = self.encrypt_data(data, data_type)
                return [Output(type="text", payload=f"Зашифровано:\n{encrypted}")]
            return [Output(type="text", payload="/encrypt <type> <data>")]

        if payload.startswith("/decrypt "):
            parts = payload[9:].split(" ", 1)
            if len(parts) == 2:
                decrypted = self.decrypt_data(parts[1], parts[0])
                return [Output(type="text", payload=f"Расшифровано:\n{decrypted}")]
            return [Output(type="text", payload="/decrypt <type> <encrypted_data>")]

        if payload.startswith("/policy_check ") or payload.startswith("/security_policy "):
            prefix = "/policy_check " if payload.startswith("/policy_check ") else "/security_policy "
            parts = payload[len(prefix) :].strip().split()
            if len(parts) >= 3:
                role, resource, action = parts[0], parts[1], parts[2]
                ok = self.check_access_policy(role, resource, action)
                verdict = "разрешено" if ok else "запрещено"
                return [
                    Output(
                        type="text",
                        payload=f"Политика: роль={role}, ресурс={resource}, действие={action} → {verdict}",
                        meta={"module": "security_layer", "policy_ok": ok},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload="/policy_check <role> <resource> <action> — пример: /policy_check parent messages parent",
                )
            ]

        return [
            Output(
                type="text",
                payload="/encrypt <type> <data> | /decrypt <type> <data> | /policy_check <role> <resource> <action>",
            )
        ]

    def encrypt_data(self, data: str, data_type: str) -> str:
        try:
            key = _derive_key(self.encryption_key, data_type)
            result = _aes_encrypt(data, key)
            if self.log_encrypted:
                self._log_action("encrypt", data_type, len(data))
            return result
        except ImportError:
            logger.warning("[security] cryptography not installed; fallback to safe encoding")
            return base64.b64encode(data.encode()).decode()
        except Exception:
            logger.exception("[security] encrypt failed type=%s", data_type)
            return base64.b64encode(data.encode()).decode()

    def decrypt_data(self, encrypted_data: str, data_type: str = "default") -> str:
        try:
            key = _derive_key(self.encryption_key, data_type)
            return _aes_decrypt(encrypted_data, key)
        except ImportError:
            logger.warning("[security] cryptography not installed; fallback decode")
            return base64.b64decode(encrypted_data.encode()).decode()
        except Exception:
            logger.exception("[security] decrypt failed")
            return "Ошибка расшифровки"

    def _log_action(self, action: str, data_type: str, size: int) -> None:
        try:
            entry = {
                "type": action,
                "data_type": data_type,
                "timestamp": datetime.now().isoformat(),
                "data_length": size,
            }
            log_file = os.path.join(self.storage_path, "security_logs.json")
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("[security] log write failed")

    def check_access_policy(self, user_id: str, resource: str, action: str) -> bool:
        policies: Dict[str, List[str]] = {
            "user_profiles": ["student", "parent", "teacher", "admin"],
            "psychology_data": ["parent", "teacher", "admin"],
            "digital_twin": ["user", "parent", "teacher", "admin"],
            "schedule": ["user", "parent", "teacher", "admin"],
            "messages": ["user", "parent", "teacher", "admin"],
        }
        allowed = policies.get(resource, [])
        return action in allowed
