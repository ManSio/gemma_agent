"""
User System Module - Модуль управления пользователями 
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

from core.json_atomic import atomic_write_json, read_json_file
from core.models import Output
from core.user_facing_plain import format_user_record_plain

logger = logging.getLogger(__name__)

class UserSystemModule:
    """Модуль управления пользователями и профилями"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Инициализация модуля"""
        self.config = config or {}
        self.storage_path = self.config.get("storage_path", "./data/users")
        self.users_file = os.path.join(self.storage_path, "users.json")
        self._ensure_storage_exists()
    
    def _ensure_storage_exists(self):
        """Обеспечить существование хранилища"""
        os.makedirs(self.storage_path, exist_ok=True)
        if not os.path.exists(self.users_file):
            atomic_write_json(self.users_file, {})
    
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        """Основной метод выполнения"""
        input_data = args.get("input", {})
        payload = input_data.get("payload", "")
        
        # Обработка команд
        if payload.startswith("/get_user "):
            user_id = payload[10:].strip()
            user_data = self.get_user(user_id)
            if user_data:
                return [Output(
                    type="text",
                    payload=format_user_record_plain(user_data),
                    meta={"module": "user_system", "action": "get_user"}
                )]
            else:
                return [Output(
                    type="text",
                    payload=f"Пользователь с ID {user_id} не найден",
                    meta={"module": "user_system", "action": "get_user", "error": "not_found"}
                )]
        elif payload.startswith("/update_user "):
            # Простая реализация обновления
            parts = payload[13:].split(" ", 1)
            if len(parts) == 2:
                user_id = parts[0]
                update_data_str = parts[1]
                try:
                    update_data = json.loads(update_data_str)
                    success = self.update_user(user_id, update_data)
                    if success:
                        return [Output(
                            type="text",
                            payload=f"Пользователь {user_id} успешно обновлён",
                            meta={"module": "user_system", "action": "update_user"}
                        )]
                    else:
                        return [Output(
                            type="text",
                            payload=f"Ошибка обновления пользователя {user_id}",
                            meta={"module": "user_system", "action": "update_user", "error": "failed"}
                        )]
                except Exception as e:
                    return [Output(
                        type="text",
                        payload=f"Ошибка парсинга данных: {str(e)}",
                        meta={"module": "user_system", "action": "update_user", "error": "parse_error"}
                    )]
            else:
                return [Output(
                    type="text",
                    payload="Использование: /update_user <user_id> {\"name\": \"Иван\"}",
                    meta={"module": "user_system"}
                )]
        else:
            # Базовые действия для команд
            return [Output(
                type="text",
                payload="Команды:\n/get_user <user_id> - получить информацию о пользователе\n/update_user <user_id> {\"field\": \"value\"} - обновить данные пользователя",
                meta={"module": "user_system"}
            )]
    
    def _load_users(self) -> Dict[str, Any]:
        raw = read_json_file(self.users_file, {})
        return raw if isinstance(raw, dict) else {}

    def _save_users(self, users: Dict[str, Any]) -> bool:
        return atomic_write_json(self.users_file, users)

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Получить информацию о пользователе"""
        try:
            return self._load_users().get(user_id, {})
        except Exception:
            logger.exception("[user_system] get_user failed user_id=%s", user_id)
            return {}
    
    def update_user(self, user_id: str, data: Dict[str, Any]) -> bool:
        """Обновить информацию о пользователе"""
        try:
            users = self._load_users()
            if user_id not in users:
                users[user_id] = {
                    "user_id": user_id,
                    "created_at": datetime.now().isoformat(),
                    "history": [],
                }
            users[user_id].update(data)
            users[user_id]["updated_at"] = datetime.now().isoformat()
            return self._save_users(users)
        except Exception:
            logger.exception("[user_system] update_user failed user_id=%s", user_id)
            return False
    
    def append_history(self, user_id: str, message: Dict[str, Any]) -> bool:
        """Добавить сообщение в историю пользователя"""
        try:
            users = self._load_users()
            if user_id not in users:
                users[user_id] = {
                    "user_id": user_id,
                    "created_at": datetime.now().isoformat(),
                    "history": [],
                }
            users[user_id]["history"].append(
                {"message": message, "timestamp": datetime.now().isoformat()}
            )
            if len(users[user_id]["history"]) > 50:
                users[user_id]["history"] = users[user_id]["history"][-50:]
            return self._save_users(users)
        except Exception:
            logger.exception("[user_system] append_history failed user_id=%s", user_id)
            return False
    
    def set_role(self, user_id: str, role: str) -> bool:
        """Установить роль пользователя"""
        try:
            users = self._load_users()
            if user_id not in users:
                users[user_id] = {
                    "user_id": user_id,
                    "created_at": datetime.now().isoformat(),
                    "history": [],
                }
            users[user_id]["role"] = role
            users[user_id]["updated_at"] = datetime.now().isoformat()
            return self._save_users(users)
        except Exception:
            logger.exception("[user_system] set_role failed user_id=%s", user_id)
            return False
    
    def link_parent(self, child_id: str, parent_id: str) -> bool:
        """Связать ребёнка с родителем"""
        try:
            users = self._load_users()
            if child_id not in users:
                users[child_id] = {
                    "user_id": child_id,
                    "created_at": datetime.now().isoformat(),
                    "history": [],
                }
            if parent_id not in users:
                users[parent_id] = {
                    "user_id": parent_id,
                    "created_at": datetime.now().isoformat(),
                    "history": [],
                }
            if "family" not in users[child_id]:
                users[child_id]["family"] = {}
            users[child_id]["family"]["parents"] = [parent_id]
            users[parent_id]["family"] = {"children": [child_id]}
            users[child_id]["updated_at"] = datetime.now().isoformat()
            users[parent_id]["updated_at"] = datetime.now().isoformat()
            return self._save_users(users)
        except Exception:
            logger.exception("[user_system] link_parent failed")
            return False