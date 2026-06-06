"""
Persona Engine Module - Движок персонажей
"""
from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    for key in ("PROJECT_ROOT", "GEMMA_PROJECT_ROOT"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            return Path(raw).resolve()
    # modules/persona_engine/module.py → корень репозитория
    return Path(__file__).resolve().parent.parent.parent


def _user_personas_path() -> Path:
    raw = (os.getenv("USER_PERSONAS_PATH") or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (_repo_root() / p).resolve()
    return (_repo_root() / "data" / "user_personas.json").resolve()

class PersonaEngineModule:
    """Движок персонажей ассистента"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Инициализация модуля"""
        self.config = config or {}
        self.persona_rules = self.config.get("persona_rules", {
            "friend_mode": {
                "name": "Друг",
                "description": "Дружелюбный, поддерживающий помощник",
                "traits": ["дружелюбный", "партнер", "поддерживающий", "открытый"],
                "responses": [
                    "Ты молодец!",
                    "Всё получится!",
                    "Это интересно!",
                    "Давай разберём это вместе!",
                    "Ты справишься!",
                    "Спасибо, что поделился этим!"
                ]
            },
            "teacher_mode": {
                "name": "Учитель",
                "description": "Строгий, но объективный учитель",
                "traits": ["строгий", "объективный", "образованный", "организованный"],
                "responses": [
                    "Правильно понял, но давай точнее",
                    "Давай проверим, как ты понял",
                    "Необходимо уточнить детали",
                    "Ты почти прав!",
                    "Давай систематизируем информацию"
                ]
            },
            "coach_mode": {
                "name": "Коуч",
                "description": "Мотивирующий наставник",
                "traits": ["мотивирующий", "наставник", "вдохновляющий", "целеположный"],
                "responses": [
                    "Ты уже на правильном пути!",
                    "Молодец, ты продвинулся!",
                    "Ты к этому способен!",
                    "Ты делаешь успех!",
                    "Я верю в тебя!"
                ]
            },
            "child_mode": {
                "name": "Ребёнок",
                "description": "Мягкий, с добрым настроением",
                "traits": ["мягкий", "доброжелательный", "поддерживающий", "приятный"],
                "responses": [
                    "Это круто!",
                    "Почему так? 😊",
                    "Надо попробовать! 🎉",
                    "Я с тобой!",
                    "Давай посмотрим!"
                ]
            },
            "neutral_mode": {
                "name": "Нейтральный",
                "description": "Объективный, стандартный помощник",
                "traits": ["нейтральный", "объективный", "сдержан", "нечрезмерный"],
                "responses": [
                    "Понял",
                    "Ясно",
                    "Хорошо",
                    "Давай разберём это",
                    "Ок",
                ]
            }
        })
        self.user_personas_file = str(_user_personas_path())
        self._ensure_storage_exists()

    def _actor_user_id(self, args: Dict[str, Any]) -> str:
        ctx = args.get("context") if isinstance(args.get("context"), dict) else {}
        uid = str((ctx or {}).get("user_id") or "").strip()
        return uid or "unknown"

    def list_persona_catalog_text(self) -> str:
        """Текст справки: все доступные режимы и как их включить."""
        lines = [
            "🎭 Доступные персонажи (режим общения)",
            "",
            "Это не модель LLM и не «стиль ответов» из /chat_style — отдельная роль тона и реплик.",
            "",
        ]
        for key in sorted(self.persona_rules.keys()):
            rule = self.persona_rules[key]
            title = rule.get("name") or key
            desc = (rule.get("description") or "").strip()
            lines.append(f"▸ `{key}` — {title}")
            if desc:
                lines.append(f"   {desc}")
            lines.append("")
        lines.append("Команды:")
        lines.append("• /personas — этот список")
        lines.append("• /get_persona — кто я в этом чате; /get_persona <user_id> — посмотреть у другого")
        lines.append("• /set_persona <ключ> — выбрать себе; /set_persona <user_id> <ключ> — выставить другому")
        return "\n".join(lines)

    def _help_text(self) -> str:
        return self.list_persona_catalog_text()

    def _ensure_storage_exists(self) -> None:
        path = Path(self.user_personas_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("persona_engine: не удалось создать каталог %s: %s", path.parent, e)
            raise
        if not path.exists():
            try:
                path.write_text("{}", encoding="utf-8")
            except OSError as e:
                logger.error(
                    "persona_engine: нет прав на %s (%s). Проверьте владельца каталога data/ "
                    "или задайте USER_PERSONAS_PATH в .env.",
                    path,
                    e,
                )
                raise
    
    async def execute(self, args: Dict[str, Any]) -> List[Any]:
        """Основной метод выполнения."""
        from core.models import Output
        from core.user_facing_plain import format_persona_plugin_plain

        input_data = args.get("input", {})
        payload = str(input_data.get("payload", "") or "").strip()
        actor = self._actor_user_id(args)

        if payload in {"/personas", "/list_personas"}:
            return [
                Output(
                    type="text",
                    payload=self.list_persona_catalog_text(),
                    meta={"module": "persona_engine", "action": "list_personas"},
                )
            ]

        if payload == "/get_persona" or payload.startswith("/get_persona "):
            rest = payload[len("/get_persona") :].strip()
            user_id = rest if rest else actor
            persona = self.get_persona(user_id)
            if persona:
                return [
                    Output(
                        type="text",
                        payload=format_persona_plugin_plain(persona),
                        meta={"module": "persona_engine", "action": "get_persona"},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload=f"Персонаж пользователя {user_id} не найден",
                    meta={"module": "persona_engine", "action": "get_persona", "error": "not_found"},
                )
            ]

        if payload == "/set_persona" or payload.startswith("/set_persona "):
            rest = payload[len("/set_persona") :].strip()
            if not rest:
                return [
                    Output(
                        type="text",
                        payload=self._help_text(),
                        meta={"module": "persona_engine", "action": "help"},
                    )
                ]
            tokens = rest.split()
            if len(tokens) == 1:
                persona_name = tokens[0].strip()
                if persona_name not in self.persona_rules:
                    return [
                        Output(
                            type="text",
                            payload=(
                                f"Неизвестный режим «{persona_name}». Открой /personas — там все ключи "
                                "(например neutral_mode, friend_mode)."
                            ),
                            meta={"module": "persona_engine", "action": "set_persona", "error": "unknown_persona"},
                        )
                    ]
                if self.set_persona(actor, persona_name):
                    p = self.get_persona(actor)
                    return [
                        Output(
                            type="text",
                            payload=(
                                f"✅ Персонаж для этого чата обновлён.\n\n"
                                f"{format_persona_plugin_plain(p)}"
                            ),
                            meta={"module": "persona_engine", "action": "set_persona"},
                        )
                    ]
                return [
                    Output(
                        type="text",
                        payload="Не удалось сохранить персонаж (проверь права на data/user_personas.json).",
                        meta={"module": "persona_engine", "action": "set_persona", "error": "failed"},
                    )
                ]
            if len(tokens) >= 2:
                user_id = tokens[0].strip()
                persona_name = tokens[1].strip()
                if persona_name not in self.persona_rules:
                    return [
                        Output(
                            type="text",
                            payload=(
                                f"Неизвестный режим «{persona_name}». Список: /personas"
                            ),
                            meta={"module": "persona_engine", "action": "set_persona", "error": "unknown_persona"},
                        )
                    ]
                if self.set_persona(user_id, persona_name):
                    p = self.get_persona(user_id)
                    return [
                        Output(
                            type="text",
                            payload=(
                                f"✅ Персонаж для пользователя {user_id} обновлён.\n\n"
                                f"{format_persona_plugin_plain(p)}"
                            ),
                            meta={"module": "persona_engine", "action": "set_persona"},
                        )
                    ]
                return [
                    Output(
                        type="text",
                        payload=f"Не удалось установить персонаж {persona_name} для {user_id}.",
                        meta={"module": "persona_engine", "action": "set_persona", "error": "failed"},
                    )
                ]

        return [
            Output(
                type="text",
                payload=self._help_text(),
                meta={"module": "persona_engine"},
            )
        ]
    
    def get_persona(self, user_id: str) -> Dict[str, Any]:
        """Получить персонаж пользователя"""
        try:
            with open(self.user_personas_file, "r", encoding="utf-8") as f:
                user_personas = json.load(f)
            
            uid = str(user_id).strip()
            if uid in user_personas:
                return user_personas[uid]
            return self._default_persona_payload(uid)
        except Exception as e:
            logger.warning("persona_engine get_persona: %s", e)
            return self._default_persona_payload(str(user_id).strip() or "unknown")

    def _default_persona_payload(self, user_id: str) -> Dict[str, Any]:
        """Персона по умолчанию для user_id, ещё не сохранённого в user_personas.json."""
        uid = (user_id or "").strip() or "unknown"
        neutral = self.persona_rules.get("neutral_mode", {})
        return {
            "user_id": uid,
            "persona": "neutral_mode",
            "name": neutral.get("name", "Нейтральный"),
            "description": neutral.get("description", ""),
            "traits": neutral.get("traits", []),
            "timestamp": datetime.now().isoformat(),
        }
    
    def set_persona(self, user_id: str, persona_name: str) -> bool:
        """Установить персонаж для пользователя"""
        try:
            with open(self.user_personas_file, "r", encoding="utf-8") as f:
                user_personas = json.load(f)
            
            # Проверка существования персонажа
            if persona_name not in self.persona_rules:
                return False
            
            # Записываем персонаж
            persona_data = self.persona_rules[persona_name]
            user_personas[user_id] = {
                "user_id": user_id,
                "persona": persona_name,
                "name": persona_data["name"],
                "description": persona_data["description"],
                "traits": persona_data["traits"],
                "timestamp": datetime.now().isoformat()
            }
            
            with open(self.user_personas_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(user_personas, f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            logger.warning("persona_engine set_persona: %s", e)
            return False
    
    def apply_persona_to_response(self, user_id: str, response: str) -> str:
        """Применить персонаж к ответу (по умолчанию без автопрефикса — иначе «Сейчас проверю» липнет к каждому ответу)."""
        mode = (os.getenv("PERSONA_PREPEND_MODE") or "off").strip().lower()
        if mode in {"off", "false", "0", "no", ""}:
            return response
        if mode == "rare" and random.random() > 0.12:
            return response

        persona = self.get_persona(user_id)
        persona_name = persona["persona"]

        if persona_name not in self.persona_rules:
            persona_name = "neutral_mode"

        persona_data = self.persona_rules[persona_name]
        responses_templates = persona_data["responses"]

        if responses_templates:
            prefix = random.choice(responses_templates)
            if not response.startswith(prefix):
                return f"{prefix} {response}"

        return response
    
    def get_appropriate_persona(self, user_id: str, group_id: str = None, role: str = None) -> Dict[str, Any]:
        """Получить соответствующий персонаж для контекста"""
        # Основной логике выбора персонажа
        
        # Если группа, всегда использовать friend_mode
        if group_id:
            return self.persona_rules["friend_mode"]
        
        # Если в личке, зависит от роли
        if role == "parent" or role == "teacher":
            return self.persona_rules["teacher_mode"]
        elif role == "student":
            return self.persona_rules["child_mode"]  # Ученик может быть дружелюбным или нейтральным
        else:
            return self.persona_rules["neutral_mode"]