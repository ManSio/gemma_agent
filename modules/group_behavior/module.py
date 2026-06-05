"""
Group Behavior Module - Модуль группового поведения
"""
import os
import json
import random
import logging
from typing import Dict, Any, List
from core.models import Output
from core.user_facing_plain import format_group_behavior_plain
from datetime import datetime

logger = logging.getLogger(__name__)


class GroupBehaviorModule:
    """Модуль группового поведения — анализ сообщений, шаблоны ответов, история групп."""

    def __init__(self, config: Dict[str, Any] = None):
        """Инициализация модуля"""
        self.config = config or {}
        self.storage_path = self.config.get("storage_path", "./data/group_behavior")
        self.group_file = os.path.join(self.storage_path, "groups.json")
        self.behavior_rules = self.config.get("behavior_rules", self._default_rules())
        self._ensure_storage_exists()

    def _default_rules(self) -> Dict[str, Any]:
        return {
            "school_group": {
                "language": "school",
                "tone": "academic",
                "style": "formal",
                "responses": [
                    "Давайте обсудим это более подробно",
                    "Я посмотрю, что у нас есть по этой теме",
                    "Можно немного глубже поговорить об этом",
                    "Кто-нибудь хочет добавить?",
                ],
            },
            "parent_group": {
                "language": "parent",
                "tone": "supportive",
                "style": "casual",
                "responses": [
                    "Понятно, расскажите подробнее",
                    "Я подумаю об этом",
                    "Какие у вас мысли по этому поводу?",
                    "Мы найдем решение вместе",
                ],
            },
            "study_group": {
                "language": "academic",
                "tone": "collaborative",
                "style": "academic",
                "responses": [
                    "Давайте проверим это вместе",
                    "Я проверю информацию",
                    "Что вы думаете по этому поводу?",
                    "Определим, что нам нужно дальше",
                ],
            },
            "normal_group": {
                "language": "casual",
                "tone": "friendly",
                "style": "casual",
                "responses": [
                    "Понимаю, расскажите подробнее",
                    "Очень интересно",
                    "Такое бывает, не переживайте",
                    "Давай разберемся совместно",
                ],
            },
        }

    def _ensure_storage_exists(self):
        """Обеспечить существование хранилища"""
        try:
            os.makedirs(self.storage_path, exist_ok=True)
            if not os.path.exists(self.group_file):
                with open(self.group_file, 'w', encoding='utf-8') as f:
                    json.dump({"groups": {}, "history": []}, f, ensure_ascii=False)
        except Exception as e:
            logger.exception("[group_behavior] storage init failed")

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.group_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.exception("[group_behavior] load failed")
            return {"groups": {}, "history": []}

    def _save(self, data: Dict[str, Any]) -> bool:
        try:
            tmp = self.group_file + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.group_file)
            return True
        except Exception as e:
            logger.exception("[group_behavior] save failed")
            return False

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        """Основной метод выполнения"""
        input_data = args.get("input", {})
        payload = input_data.get("payload", "")

        if payload.startswith("/handle_group_message "):
            parts = payload[22:].split(" ", 1)
            if len(parts) == 2:
                group_id = parts[0]
                message = parts[1]
                result = self.handle_group_message(group_id, message)
                return [Output(
                    type="text",
                    payload=format_group_behavior_plain(result),
                    meta={"module": "group_behavior", "action": "handle_group_message"}
                )]
            else:
                return [Output(
                    type="text",
                    payload="Использование: /handle_group_message <group_id> <message>",
                    meta={"module": "group_behavior"}
                )]

        elif payload.startswith("/generate_group_reply "):
            context_str = payload[len("/generate_group_reply ") :].strip()
            if context_str:
                try:
                    context = json.loads(context_str)
                    reply = self.generate_group_reply(context)
                    return [Output(
                        type="text",
                        payload=(
                            "Ответ по шаблону группы (не LLM, только slash /generate_group_reply):\n"
                            f"{reply}"
                        ),
                        meta={
                            "module": "group_behavior",
                            "action": "generate_group_reply",
                            "reply_source": "template",
                        },
                    )]
                except json.JSONDecodeError as e:
                    return [Output(
                        type="text",
                        payload=f"Ошибка парсинга JSON контекста: {str(e)}",
                        meta={"module": "group_behavior", "action": "generate_group_reply", "error": "parse_error"}
                    )]
            else:
                return [Output(
                    type="text",
                    payload="Использование: /generate_group_reply <context_json>",
                    meta={"module": "group_behavior"}
                )]

        elif payload.startswith("/group_stats "):
            group_id = payload[13:].strip()
            stats = self._group_stats(group_id)
            return [Output(
                type="text",
                payload=f"Статистика группы {group_id}:\n- Сообщений: {stats.get('messages_count', 0)}\n- Вмешательств: {stats.get('interventions', 0)}\n- Тип: {stats.get('group_type', 'неизвестно')}",
                meta={"module": "group_behavior", "action": "group_stats"}
            )]

        else:
            return [Output(
                type="text",
                payload="Команды:\n/handle_group_message <group_id> <message> — обработать сообщение\n/generate_group_reply <context_json> — сгенерировать ответ\n/group_stats <group_id> — статистика группы",
                meta={"module": "group_behavior"}
            )]

    def _get_group_type(self, group_id: str) -> str:
        """Определить тип группы по ID"""
        low = group_id.lower()
        if "school" in low or "урок" in low:
            return "school_group"
        elif "parent" in low or "родитель" in low:
            return "parent_group"
        elif "study" in low or "учеб" in low:
            return "study_group"
        else:
            return "normal_group"

    def handle_group_message(self, group_id: str, message: str) -> Dict[str, Any]:
        """Обработать сообщение в группе с сохранением истории"""
        try:
            group_type = self._get_group_type(group_id)
            should_intervene = self.should_intervene(group_type, message)

            result = {
                "group_id": group_id,
                "group_type": group_type,
                "message": message[:100] + "..." if len(message) > 100 else message,
                "timestamp": datetime.now().isoformat(),
                "behavior_analysis": self._analyze_behavior(group_type, message),
                "should_intervene": should_intervene,
                "response_template": self._pick_template_phrase(group_type),
            }

            # Сохраняем в историю
            data = self._load()
            data.setdefault("groups", {}).setdefault(group_id, {
                "type": group_type,
                "messages_count": 0,
                "interventions": 0,
                "first_seen": datetime.now().isoformat(),
            })
            data["groups"][group_id]["messages_count"] += 1
            if should_intervene:
                data["groups"][group_id]["interventions"] += 1
            data["groups"][group_id]["last_message"] = datetime.now().isoformat()

            data.setdefault("history", []).append({
                "group_id": group_id,
                "message_preview": message[:80],
                "result": result,
                "timestamp": datetime.now().isoformat(),
            })
            # Ограничиваем историю 200 записями
            if len(data["history"]) > 200:
                data["history"] = data["history"][-200:]

            self._save(data)
            return result

        except Exception as e:
            logger.exception("[group_behavior] handle_group_message failed group_id=%s", group_id)
            return {"error": str(e), "group_id": group_id}

    def _analyze_behavior(self, group_type: str, message: str) -> Dict[str, Any]:
        """Анализировать поведение в группе"""
        try:
            low = message.lower()
            social_cues = ["думаю", "посмотрю", "вижу", "понимаю"]
            process_indicators = ["так", "надо", "ещё", "пока"]

            social_cues_count = sum(1 for cue in social_cues if cue in low)
            process_indicators_count = sum(1 for cue in process_indicators if cue in low)

            return {
                "social_cues": social_cues_count > 0,
                "process_indicators": process_indicators_count > 0,
                "engagement_level": "high" if social_cues_count > 0 else "medium",
                "conversation_flow": "natural" if process_indicators_count > 0 else "direct",
            }
        except Exception as e:
            logger.exception("[group_behavior] analyze_behavior failed")
            return {"engagement_level": "unknown", "error": str(e)}

    def should_intervene(self, group_type: str, message: str) -> bool:
        """Определить, нужно ли вмешаться"""
        try:
            indicators = ["помогите", "не могу", "нужна помощь", "помощь", "не понимаю"]
            return any(indicator in message.lower() for indicator in indicators)
        except Exception:
            return False

    def generate_group_reply(self, context: Dict[str, Any]) -> str:
        """Сгенерировать ответ в группе"""
        try:
            group_type = context.get("group_type", "normal_group")
            message = context.get("message", "")
            template = self._get_response_template(group_type)

            responses = []
            random_elements = [
                "Сек, думаю...",
                "Посмотрю...",
                "Хм, интересная тема",
                "Подожди немного...",
                "Разберёмся...",
            ]

            if len(message) > 100:
                responses.append(random.choice(random_elements))
            responses.append(random.choice(template))

            body = " ".join(responses)
            return f"[шаблон] {body}"
        except Exception as e:
            logger.exception("[group_behavior] generate_group_reply failed")
            return "[шаблон] Извините, не удалось сгенерировать ответ."

    def _get_response_template(self, group_type: str) -> List[str]:
        """Получить шаблон ответа для типа группы"""
        group_rules = self.behavior_rules.get(group_type, {})
        return group_rules.get("responses", ["Как интересно", "Здорово"])

    def _pick_template_phrase(self, group_type: str) -> str:
        opts = self._get_response_template(group_type)
        return random.choice(opts) if opts else ""

    def _group_stats(self, group_id: str) -> Dict[str, Any]:
        """Получить статистику группы из истории"""
        try:
            data = self._load()
            group = data.get("groups", {}).get(group_id, {})
            return {
                "group_id": group_id,
                "group_type": group.get("type", self._get_group_type(group_id)),
                "messages_count": group.get("messages_count", 0),
                "interventions": group.get("interventions", 0),
                "first_seen": group.get("first_seen", ""),
                "last_message": group.get("last_message", ""),
            }
        except Exception as e:
            logger.exception("[group_behavior] group_stats failed group_id=%s", group_id)
            return {"error": str(e), "group_id": group_id}

    def get_group_behavior(self, group_id: str) -> Dict[str, Any]:
        """Контекст для orchestrator/brain — метаданные группы, не slash-шаблон."""
        gid = str(group_id or "").strip()
        if not gid:
            return {}
        stats = self._group_stats(gid)
        if stats.get("error"):
            return {}
        gtype = str(stats.get("group_type") or self._get_group_type(gid))
        templates = self._get_response_template(gtype)
        sample = random.choice(templates) if templates else ""
        return {
            "group_id": gid,
            "group_type": gtype,
            "messages_count": int(stats.get("messages_count") or 0),
            "interventions": int(stats.get("interventions") or 0),
            "last_message": stats.get("last_message") or "",
            "template_sample": sample,
            "reply_source": "brain",
            "hint": (
                "GROUP_BEHAVIOR: ответ в группе даёт brain/LLM по контексту; "
                "шаблоны /generate_group_reply — только по явному slash."
            ),
        }
