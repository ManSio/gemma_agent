"""
Security Layer for Universal Social Assistant
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional
from cryptography.fernet import Fernet
from datetime import datetime
from core.models import User
import base64

logger = logging.getLogger(__name__)


def _normalize_fernet_key(raw: Optional[str]) -> str:
    """Убрать BOM, переносы, кавычки из .env / копипасты."""
    if not raw:
        return ""
    s = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    return s


class SecurityLayer:
    """Complete security implementation with encryption and access control"""
    
    def __init__(self):
        self.encryption_key = _normalize_fernet_key(os.getenv("SECURITY_AES_KEY"))
        if not self.encryption_key:
            raise ValueError("SECURITY_AES_KEY environment variable is required")
        key_b = self.encryption_key.encode("utf-8")
        try:
            self.fernet = Fernet(key_b)
        except ValueError as e:
            raise ValueError(
                "SECURITY_AES_KEY должен быть валидным ключом Fernet "
                "(строка из Fernet.generate_key(), обычно 44 символа base64). "
                "Сгенерировать: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from e
        self.security_policy = self._load_security_policy()
    
    def _load_security_policy(self) -> Dict[str, Any]:
        return {
            "roles": {
                "child": {
                    "read": ["user_profile", "schedule", "progress"],
                    "write": ["schedule", "progress"],
                    "admin": []
                },
                "parent": {
                    "read": ["user_profile", "schedule", "progress", "psychology", "digital_twin"],
                    "write": ["schedule", "progress"],
                    "admin": []
                },
                "teacher": {
                    "read": ["user_profile", "schedule", "progress", "psychology", "digital_twin"],
                    "write": ["schedule", "progress", "psychology", "digital_twin"],
                    "admin": []
                },
                "admin": {
                    "read": ["user_profile", "schedule", "progress", "psychology", "digital_twin", "settings"],
                    "write": ["user_profile", "schedule", "progress", "psychology", "digital_twin", "settings"],
                    "admin": ["all"]
                },
                "system": {
                    "read": ["user_profile", "schedule", "progress", "psychology", "digital_twin"],
                    "write": [],
                    "admin": []
                }
            },
            "resources": {
                "user_profile": ["child", "parent", "teacher", "admin"],
                "schedule": ["child", "parent", "teacher", "admin"],
                "progress": ["child", "parent", "teacher", "admin"],
                "psychology": ["parent", "teacher", "admin"],
                "digital_twin": ["parent", "teacher", "admin"],
                "settings": ["admin"]
            }
        }
    
    def encrypt_data(self, data: str) -> str:
        try:
            if not data:
                return ""
            encrypted_data = self.fernet.encrypt(data.encode())
            return encrypted_data.decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
    
    def decrypt_data(self, encrypted_data: str) -> str:
        try:
            if not encrypted_data:
                return ""
            decrypted_data = self.fernet.decrypt(encrypted_data.encode())
            return decrypted_data.decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
    
    def check_permission(self, user: User, action: str, resource: str) -> bool:
        try:
            user_role = getattr(user, "role", None)
            policy = self.security_policy
            
            if not user_role or user_role not in policy["roles"]:
                logger.warning(f"Unknown role: {user_role}, denying access")
                return False
            
            role_permissions = policy["roles"][user_role]
            
            if action not in role_permissions:
                return False
            
            if role_permissions.get("admin") == ["all"]:
                return True
            
            if resource in policy["resources"]:
                allowed_roles = policy["resources"][resource]
                if user_role not in allowed_roles:
                    return False
            
            permissions = role_permissions.get(action, [])
            if permissions and resource not in permissions and "all" not in permissions:
                return False
            
            return True
        except Exception as e:
            logger.error(f"Permission check error: {e}")
            return False
    
    def log_access(self, user_id: int, resource: str, action: str, 
                   success: bool = True, details: str = ""):
        try:
            timestamp = datetime.now().isoformat()
            access_log = {
                "user_id": user_id,
                "resource": resource,
                "action": action,
                "success": success,
                "timestamp": timestamp,
                "details": details
            }
            logger.info(f"ACCESS_LOG: {json.dumps(access_log)}")
        except Exception as e:
            logger.error(f"Failed to log access: {e}")
    
    def get_encrypted_fields(self) -> List[str]:
        return ["psychology", "digital_twin", "parent_child_links", "user_profile", "progress", "schedule"]
    
    def is_encryption_enabled(self) -> bool:
        return (self.encryption_key is not None
                and len(self.encryption_key) == 44
                and hasattr(self, 'fernet') and self.fernet is not None)


security_layer = SecurityLayer()
