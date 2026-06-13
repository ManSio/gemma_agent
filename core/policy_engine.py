"""
Policy Engine для контроля доступа и ограничений
"""
import logging
import os
from typing import Dict, Any, List
from enum import Enum
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _call_history_retention_minutes() -> int:
    try:
        return max(1, min(120, int((os.getenv("POLICY_CALL_HISTORY_RETENTION_MIN") or "10").strip())))
    except (TypeError, ValueError):
        return 10


class Role(str, Enum):
    """Роли пользователей"""
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"

class PolicyEngine:
    """Движок политики для контроля доступа"""
    
    def __init__(self):
        # Конфигурация политик (в будущем будет из файла/базы)
        self.policies = {
            "module_access": {
                "default": {
                    "allowed_roles": [Role.USER, Role.ADMIN, Role.SYSTEM],
                    "rate_limits": {
                        "per_minute": 100,
                        "per_hour": 1000
                    }
                }
            },
            "module_behavior": {
                "default": {
                    "allowed_modules": [],
                    "fallback_behavior": "disabled"
                }
            }
        }
        
        # История вызовов для контроля частоты
        self.call_history = {}
    
    def check_module_access(self, module_name: str, user_role: Role, context: Dict[str, Any]) -> bool:
        """Проверить доступ к модулю"""
        try:
            policy = self.policies["module_access"].get("default", {})
            
            # Проверяем разрешенные роли
            if user_role not in policy.get("allowed_roles", []):
                logger.warning(f"Role {user_role} not allowed to access module {module_name}")
                return False
            
            # Проверяем частоту вызовов
            if self._check_rate_limit(user_role, module_name):
                logger.warning(f"Rate limit exceeded for role {user_role} and module {module_name}")
                return False
            
            # Записываем вызов
            self._record_call(user_role, module_name)
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking module access: {e}")
            return False
    
    def _check_rate_limit(self, user_role: Role, module_name: str) -> bool:
        """
        Проверить лимиты частоты вызовов через call_history.

        Смотрит количество вызовов за последние 60 секунд (per_minute)
        и за последний час (per_hour). Если превышен хотя бы один — return True.
        """
        key = f"{user_role}_{module_name}"
        now = datetime.now()
        policy = self.policies.get("module_access", {}).get("default", {})
        limits = policy.get("rate_limits", {})

        per_minute = int(limits.get("per_minute", 100))
        per_hour = int(limits.get("per_hour", 1000))

        # Получаем историю и чистим
        history = self.call_history.get(key, [])
        cutoff_minute = now - timedelta(minutes=1)
        cutoff_hour = now - timedelta(hours=1)

        calls_last_minute = sum(1 for t in history if t > cutoff_minute)
        calls_last_hour = sum(1 for t in history if t > cutoff_hour)

        if calls_last_minute >= per_minute:
            logger.warning(
                "Rate limit per-minute exceeded for %s: %d >= %d",
                key, calls_last_minute, per_minute,
            )
            return True

        if calls_last_hour >= per_hour:
            logger.warning(
                "Rate limit per-hour exceeded for %s: %d >= %d",
                key, calls_last_hour, per_hour,
            )
            return True

        self._prune_call_history()
        return False
    
    def _prune_call_history(self) -> None:
        """Drop expired timestamps and remove empty keys from call_history."""
        cutoff = datetime.now() - timedelta(minutes=_call_history_retention_minutes())
        stale_keys = []
        for key, entries in list(self.call_history.items()):
            fresh = [call_time for call_time in entries if call_time > cutoff]
            if fresh:
                self.call_history[key] = fresh
            else:
                stale_keys.append(key)
        for key in stale_keys:
            self.call_history.pop(key, None)
    
    def _record_call(self, user_role: Role, module_name: str):
        """Записать вызов в историю"""
        key = f"{user_role}_{module_name}"
        if key not in self.call_history:
            self.call_history[key] = []
        
        self.call_history[key].append(datetime.now())
        self._prune_call_history()
    
    def get_allowed_modules(self, user_role: Role, context: Dict[str, Any]) -> List[str]:
        """Получить список разрешённых модулей для роли.

        Если в политике ``allowed_modules`` пусто — разрешены все имена из
        ``context['all_module_names']`` (режим по умолчанию).
        """
        try:
            all_names = list(context.get("all_module_names") or [])
            behavior = self.policies.get("module_behavior", {}).get("default", {})
            whitelist = behavior.get("allowed_modules") or []
            if not whitelist:
                return all_names
            return [m for m in whitelist if m in all_names]
        except Exception as e:
            logger.error("get_allowed_modules: %s", e)
            return list(context.get("all_module_names") or [])
    
    def apply_policy(self, policy_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Применить политику"""
        policy = self.policies.get(policy_type, {})
        if policy:
            # Реализация применяет политику к контексту
            return policy
        return {}