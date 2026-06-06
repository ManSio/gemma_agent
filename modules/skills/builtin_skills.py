from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.code_intake import CodeIntakeLayer
from modules.skills.skill_interface import Skill, SkillResult


# ── Утилиты ──────────────────────────────────────────────────────────────────

def _extract_nouns(text: str) -> List[str]:
    """Извлечение ключевых слов (грубое правило: слова >3 букв, начинаются с заглавной или кириллические)."""
    words = re.findall(r"[А-ЯA-Z][а-яa-zА-ЯA-Z]{2,}", text or "")
    return [w.lower() for w in words if w.lower() not in {"что", "как", "для", "где", "это"}]


def _guess_language(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "unknown"
    ru = sum(1 for c in t if 'а' <= c.lower() <= 'я' or c in 'ёЁ')
    en = sum(1 for c in t if 'a' <= c.lower() <= 'z')
    return "ru" if ru > en else "en" if en > ru else "mixed"


def _emotion_markers(text: str) -> List[str]:
    t = (text or "").lower()
    markers = []
    if any(w in t for w in ("груст", "плох", "депресс", "одинок", "устал", "обид")):
        markers.append("sad")
    if any(w in t for w in ("зол", "бес", "раздраж", "недовол")):
        markers.append("angry")
    if any(w in t for w in ("рад", "счаст", "отлич", "крут", "класс")):
        markers.append("happy")
    if any(w in t for w in ("тревож", "боюс", "страх", "волну", "пережив")):
        markers.append("anxious")
    if any(w in t for w in ("?")):
        markers.append("confused")
    return markers


# ── Аналитические скиллы (не заглушки) ───────────────────────────────────────

class TranslatorSkill(Skill):
    name = "translator"
    focus = "translation"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        src_lang = _guess_language(user_text)
        tgt_lang = "ru" if src_lang == "en" else "en" if src_lang == "ru" else src_lang
        # Проверка user_facts на предпочтительный язык
        preferred = (user_facts or {}).get("language", "")
        if preferred and preferred in ("ru", "en", "be", "uk", "pl", "de", "fr"):
            tgt_lang = preferred
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "source_language": src_lang,
                "target_language": tgt_lang,
                "terms": _extract_nouns(user_text),
            },
            hint=f"Переведи с {src_lang} на {tgt_lang}. Сохрани тон и технический контекст если есть.",
        )


class TeacherSkill(Skill):
    name = "teacher"
    focus = "education"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        topics = _extract_nouns(user_text)
        level = (user_facts or {}).get("education_level", "beginner")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "level": level,
                "language": _guess_language(user_text),
            },
            hint=f"Объясни {', '.join(topics) if topics else 'тему'} простыми шагами "
                 f"(уровень: {level}), добавь 1-2 примера и короткий self-check вопрос.",
        )


class ProgrammerSkill(Skill):
    name = "programmer"
    focus = "coding"

    def __init__(self) -> None:
        self._code = CodeIntakeLayer()

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        result = self._code.engineer_cycle(context, user_text)
        return SkillResult(
            result={"skill": self.name, "focus": self.focus, **result},
            hint=(
                "Инженерный цикл: анализ -> priority/risk -> patch pack (unified diff) -> lint/test plan. "
                "Сфокусируйся на минимальном безопасном патче, выполняй high-priority сначала."
            ),
        )


class FinanceHelperSkill(Skill):
    name = "finance_helper"
    focus = "finance"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        numbers = re.findall(r"\d+(?:[.,]\d+)?", user_text or "")
        currencies = re.findall(r"(?i)(usd|eur|rub|byn|pln|gbp|₽|\$|€|\$)", user_text or "")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "mentioned_numbers": numbers[:10],
                "currencies": list(set(c.lower() for c in currencies)),
                "has_budget_question": "бюджет" in (user_text or "").lower() or "budget" in (user_text or "").lower(),
            },
            hint="Дай практичный ответ без финансовых обещаний. Если есть числа — проверь расчёты.",
        )


class TaskPlannerSkill(Skill):
    name = "task_planner"
    focus = "planning"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        # Определяем количество шагов по объёму запроса
        t = (user_text or "").strip()
        steps_count = min(8, max(2, len(t) // 80 + 1))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "estimated_steps": steps_count,
                "key_nouns": _extract_nouns(user_text),
                "language": _guess_language(user_text),
            },
            hint=f"Разбей цель на {steps_count} конкретных шагов с приоритетом и оценкой времени.",
        )


class ScheduleHelperSkill(Skill):
    name = "schedule_helper"
    focus = "schedule"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        # Ищем временные маркеры
        time_refs = re.findall(
            r"(?i)(понедельник|вторник|сред|четверг|пятниц|суббот|воскресен|"
            r"сегодня|завтра|послезавтра|monday|tuesday|wednesday|thursday|friday|"
            r"saturday|sunday|today|tomorrow|\d{1,2}:\d{2}|\d{1,2}ч)",
            user_text or "",
        )
        weekly_hint = ""
        try:
            from core.schedule_nl import parse_weekly_schedule

            uid = str((context or {}).get("user_id") or "")
            if parse_weekly_schedule(user_text or "", user_id=uid):
                weekly_hint = (
                    " Запрос уже можно оформить как еженедельное NL-напоминание "
                    "(«каждый понедельник в 09:00 …») — бот сохранит повтор."
                )
        except Exception:
            pass
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "time_references": list(set(time_refs)),
                "duration_estimate_min": max(15, len((user_text or "").split()) * 5),
            },
            hint="Составь реалистичный план с буферами между задачами." + weekly_hint,
        )


class LearningHelperSkill(Skill):
    name = "learning_helper"
    focus = "learning"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        topics = _extract_nouns(user_text)
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "language": _guess_language(user_text),
            },
            hint=f"Предложи mini-план обучения по {', '.join(topics[:3]) if topics else 'теме'}: "
                 f"база -> практика -> интервальное повторение.",
        )


class CoachSkill(Skill):
    name = "coach"
    focus = "motivation"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        emotions = _emotion_markers(user_text)
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detected_emotions": emotions,
                "needs_encouragement": "sad" in emotions or "anxious" in emotions,
            },
            hint="Поддержи мягко, признай эмоции, дай 1-2 конкретных шага а не общие слова.",
        )


class LawyerSkill(Skill):
    name = "lawyer"
    focus = "legal"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        domain = "unknown"
        if any(w in t for w in ("труд", "работ", "увольн", "зарплат", "трудов")):
            domain = "labor"
        elif any(w in t for w in ("жил", "квартир", "аренд", "сосед", "жкх")):
            domain = "housing"
        elif any(w in t for w in ("наслед", "завещан", "имуществ")):
            domain = "inheritance"
        elif any(w in t for w in ("кредит", "долг", "займ", "банк")):
            domain = "finance_law"
        elif any(w in t for w in ("налог", "декларац", "ндс", "ндфл")):
            domain = "tax"
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "legal_domain": domain,
                "needs_disclaimer": True,
            },
            hint=f"Сфера: {domain}. Объясни осторожно, без юридических гарантий. "
                 "Укажи что это общая информация, не консультация.",
        )


class MarketerSkill(Skill):
    name = "marketer"
    focus = "marketing"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        has_product = any(w in t for w in ("продукт", "товар", "услуг", "product", "service"))
        has_audience = any(w in t for w in ("аудитор", "клиент", "buyer", "user", "customer"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "has_product_description": has_product,
                "has_audience_info": has_audience,
                "key_terms": _extract_nouns(user_text),
            },
            hint="Сформируй гипотезы: позиционирование -> аудитория -> каналы -> KPI.",
        )


class AnalystSkill(Skill):
    name = "analyst"
    focus = "analytics"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        numbers = re.findall(r"\d+(?:[.,]\d+)?", user_text or "")
        has_data = any(w in (user_text or "").lower() for w in ("данн", "метрик", "показ", "стат", "data", "metric"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "has_data_reference": has_data,
                "numbers_found": numbers[:8],
                "key_entities": _extract_nouns(user_text),
            },
            hint="Структурируй: контекст -> данные -> выводы. Укажи допущения.",
        )


class DevOpsAssistantSkill(Skill):
    name = "devops_assistant"
    focus = "devops"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        tech_stack = []
        for tech in ("docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins",
                     "gitlab", "github", "nginx", "postgresql", "redis", "rabbitmq",
                     "prometheus", "grafana", "elastic", "istio", "helm"):
            if tech in t:
                tech_stack.append(tech)
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detected_tech": tech_stack,
                "has_ci_cd_question": "ci" in t or "cd" in t or "депло" in t or "релиз" in t,
            },
            hint=f"Техстек: {', '.join(tech_stack) if tech_stack else 'не указан'}. "
                 "Предложи минимальное рабочее решение, без овер-инжиниринга.",
        )


class TelegramBotEngineerSkill(Skill):
    name = "telegram_bot_engineer"
    focus = "telegram_engineering"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        patterns = {
            "handler": any(w in t for w in ("handler", "хендлер", "обработк", "dispatch")),
            "state": any(w in t for w in ("fsm", "state", "состоян", "контекст")),
            "inline_keyboard": any(w in t for w in ("inline", "кнопк", "button", "клавиатур")),
            "media": any(w in t for w in ("photo", "video", "document", "media", "фото", "видео", "файл")),
            "middleware": any(w in t for w in ("middleware", "middleware", "filter")),
        }
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detected_patterns": patterns,
            },
            hint="Дай пример кода на aiogram 3 с типизацией. Если паттерн не указан - предложи общую архитектуру.",
        )


class PsychologistSoftSkill(Skill):
    name = "psychologist_soft"
    focus = "soft_psychology"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        emotions = _emotion_markers(user_text)
        psych = context.get("psychology") if isinstance(context.get("psychology"), dict) else {}
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detected_emotions": emotions,
                "has_professional_concern": any(
                    w in (user_text or "").lower()
                    for w in ("суицид", "самоубийств", "депрессия", "паник", "травм", "психиатр")
                ),
            },
            hint=f"Эмоции: {emotions}. Будь эмпатичен, не ставь диагнозов. "
                 "При серьёзных проблемах — предложи обратиться к специалисту.",
        )


class FinanceHelper2Skill(Skill):
    name = "finance_helper_2"
    focus = "finance_advanced"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        numbers = re.findall(r"\d+(?:[.,]\d+)?", user_text or "")
        t = (user_text or "").lower()
        has_risk = any(w in t for w in ("риск", "risk", "страхов", "гарант", "forecast"))
        has_scenario = any(w in t for w in ("сценар", "scenario", "оптимист", "пессимист", "best", "worst"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detailed": True,
                "numbers": numbers[:15],
                "has_risk_context": has_risk,
                "has_scenario_request": has_scenario,
            },
            hint="Сделай детальную декомпозицию с рисками и сценариями (best/worst/base).",
        )


class NewsHelperSkill(Skill):
    name = "news_helper"
    focus = "news"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        topics = _extract_nouns(user_text)
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "language": _guess_language(user_text),
            },
            hint=(
                "Дай содержательную сводку: на каждый пункт 3–4 предложения — что случилось, "
                "кто участники, суть и контекст; только факты из материалов, без URL."
            ),
        )


class HealthHelperSkill(Skill):
    name = "health_helper"
    focus = "health"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        symptoms = re.findall(
            r"(?i)(бол[еьит]|температур|кашл|насморк|голов[ау]|дискомфорт|"
            r"усталост|слабост|тошнот|головокруж|больно|давлен[ие]|пульс|сыпь|"
            r"дав[иит]|кол[и]?т|но[е]?т|тян[е]?т|жж[е]?т|стреля[е]?т)",
            user_text or "",
        )
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detected_symptoms": list(set(s.lower() for s in symptoms)),
                "needs_doctor_disclaimer": bool(symptoms),
            },
            hint="Предложи общую информацию, НЕ ставь диагноз. При симптомах — рекомендую врача.",
        )


class RecipeCookingSkill(Skill):
    name = "recipe_cooking_helper"
    focus = "cooking"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        ingredients = re.findall(
            r"(?i)(куриц|говяд|свин[и]?н|рыб|овощ|фрукт|картошк|рис|макарон|гречк|"
            r"лук|морков|капуст|помидор|огурец|яйц|молок|масл[оа]|сметан|сыр|мук|сахар"
            r"|соль|перец|зелен|укроп|петруш|чеснок|лимон|яблок)",
            user_text or "",
        )
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "detected_ingredients": list(set(ingredients)),
                "language": _guess_language(user_text),
            },
            hint=f"Ингредиенты: {', '.join(set(ingredients[:8])) if ingredients else 'не указаны'}. "
                 "Дай простой пошаговый рецепт с временем приготовления.",
        )


# ── Новые скиллы: математика, наука, повседневность ──────────────────────────

class MathReasoningSkill(Skill):
    name = "math_reasoning"
    focus = "math"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        numbers = re.findall(r"\d+(?:[.,]\d+)?", user_text or "")
        ops = []
        t = (user_text or "").lower()
        if any(w in t for w in ("+", "plus", "сумм", "слож", "прибав")):
            ops.append("addition")
        if any(w in t for w in ("-", "minus", "вычит", "разност")):
            ops.append("subtraction")
        if any(w in t for w in ("*", "×", "умнож", "произвед")):
            ops.append("multiplication")
        if any(w in t for w in ("/", "÷", "дел", "частн")):
            ops.append("division")
        if any(w in t for w in ("процент", "percent", "%")):
            ops.append("percentage")
        if any(w in t for w in ("степен", "квадрат", "√", "корен", "power")):
            ops.append("exponent")
        if any(w in t for w in ("логик", "if", "then", "следов", "вывод", "logic")):
            ops.append("logic")
        if any(w in t for w in ("вероятност", "шанс", "probability")):
            ops.append("probability")
        if any(w in t for w in ("функци", "производ", "интеграл", "предел")):
            ops.append("calculus")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "numbers": numbers[:10],
                "operations": ops,
                "has_formula": "=" in (user_text or ""),
            },
            hint=f"Операции: {', '.join(ops) if ops else 'разбор'}. "
                 "Покажи пошаговое решение, объясни каждый шаг.",
        )


class PhysicsEngineerSkill(Skill):
    name = "physics_engineer"
    focus = "physics_engineering"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        domains = []
        if any(w in t for w in ("механик", "движени", "скорост", "ускорен", "сил", "mass", "f=ma")):
            domains.append("mechanics")
        if any(w in t for w in ("электричеств", "ток", "напряжен", "сопротивлен", "мощност")):
            domains.append("electricity")
        if any(w in t for w in ("термодинам", "теплот", "температур", "энерги")):
            domains.append("thermodynamics")
        if any(w in t for w in ("оптик", "свет", "линз", "отражен")):
            domains.append("optics")
        if any(w in t for w in ("квантов", "атом", "электрон")):
            domains.append("quantum")
        if any(w in t for w in ("сопромат", "прочност", "балк", "конструкц")):
            domains.append("strength_materials")
        if any(w in t for w in ("гидравлик", "жидкост", "давлен")):
            domains.append("hydraulics")
        numbers = re.findall(r"\d+(?:[.,]\d+)?", user_text or "")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "domains": domains,
                "numbers": numbers[:8],
            },
            hint=f"Разделы: {', '.join(domains) if domains else 'физика'}. "
                 "Объясни принцип, дай формулу и численный пример если возможно.",
        )


class GeographyTravelSkill(Skill):
    name = "geography_travel"
    focus = "geography_travel"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        countries = re.findall(
            r"(?i)(росси[яи]|беларус|украин|польш|германи|франци|испани|итали|"
            r"кита[йя]|япони|сша|usa|uk|великобритан|канад|австрали|бразили|инди[яи]|"
            r"турци|египет|таиланд|вьетнам|оаэ|грузи[яи]|армени[яи]|казахстан)",
            user_text or "",
        )
        has_visa = any(w in t for w in ("виз", "visa", "паспорт", "загран"))
        has_flight = any(w in t for w in ("билет", "авиа", "рейс", "flight", "plane"))
        has_hotel = any(w in t for w in ("отель", "гостиниц", "hotel", "бронирова"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "countries": list(set(c.capitalize() for c in countries)),
                "visa_question": has_visa,
                "flight_question": has_flight,
                "accommodation_question": has_hotel,
            },
            hint=f"Страны: {', '.join(set(c.capitalize() for c in countries[:5])) if countries else 'не указаны'}. "
                 "Дай практичную информацию: климат, виза, транспорт, валюта.",
        )


class HistoryCultureSkill(Skill):
    name = "history_culture"
    focus = "history_culture"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        eras = []
        if any(w in t for w in ("древн", "антич", "рим", "греци", "египет")):
            eras.append("ancient")
        if any(w in t for w in ("средневеков", "рыцар", "феода")):
            eras.append("medieval")
        if any(w in t for w in ("нов", "возрожден", "ренессан", "просвещен")):
            eras.append("renaissance")
        if any(w in t for w in ("xx век", "20 век", "перва", "втора", "холодн", "советск")):
            eras.append("modern")
        years = re.findall(r"\d{3,4}", user_text or "")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "eras": eras,
                "years_mentioned": [y for y in years if 1000 <= int(y) <= 2100][:6],
            },
            hint=f"Эпохи: {', '.join(eras) if eras else 'история'}. "
                 "Дай контекст: даты, причины, последствия. Без пустых обобщений.",
        )


class LiteratureArtSkill(Skill):
    name = "literature_art"
    focus = "literature_art"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        has_book = any(w in t for w in ("книг", "роман", "рассказ", "повест", "book", "novel", "автор"))
        has_music = any(w in t for w in ("музык", "песн", "альбом", "композитор", "music", "song"))
        has_art = any(w in t for w in ("картин", "живопис", "скульптур", "art", "painting"))
        has_film = any(w in t for w in ("фильм", "кино", "сериал", "movie", "film", "cinema"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "literature": has_book,
                "music": has_music,
                "visual_art": has_art,
                "film": has_film,
            },
            hint="Дай разбор с контекстом, жанром и значением. "
                 "Если конкретное произведение — краткий сюжет/анализ.",
        )


class BiologyNatureSkill(Skill):
    name = "biology_nature"
    focus = "biology_nature"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        domains = []
        if any(w in t for w in ("растен", "цвет", "дерев", "садов", "огород")):
            domains.append("botany")
        if any(w in t for w in ("животн", "звер", "птиц", "рыб", "насеком")):
            domains.append("zoology")
        if any(w in t for w in ("генетик", "днк", "ген", "хромосом")):
            domains.append("genetics")
        if any(w in t for w in ("эколог", "климат", "загрязнен", "природ")):
            domains.append("ecology")
        if any(w in t for w in ("анатом", "орган", "клетк", "ткань", "физиологи")):
            domains.append("anatomy")
        if any(w in t for w in ("эволюци", "дарвин", "естествен")):
            domains.append("evolution")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "domains": domains,
            },
            hint=f"Раздел: {', '.join(domains) if domains else 'биология'}. "
                 "Объясни просто, без излишней терминологии.",
        )


class TechGadgetsSkill(Skill):
    name = "tech_gadgets"
    focus = "tech_gadgets"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        categories = []
        if any(w in t for w in ("телефон", "смартфон", "iphone", "android", "phone")):
            categories.append("smartphone")
        if any(w in t for w in ("ноутбук", "laptop", "macbook", "thinkpad")):
            categories.append("laptop")
        if any(w in t for w in ("наушник", "колонк", "audio", "звук")):
            categories.append("audio")
        if any(w in t for w in ("монитор", "экран", "display")):
            categories.append("display")
        if any(w in t for w in ("видеокарт", "gpu", "процессор", "cpu", "ssd", "ram")):
            categories.append("components")
        if any(w in t for w in ("умн", "smart", "iot", "wearable")):
            categories.append("smart_home")
        has_compare = any(w in t for w in ("сравн", "vs", "или", "лучш", "compare"))
        has_budget = any(w in t for w in ("цен", "бюджет", "дешев", "дорог", "price"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "categories": categories,
                "needs_comparison": has_compare,
                "budget_mentioned": has_budget,
            },
            hint=f"Категории: {', '.join(categories) if categories else 'техника'}. " +
                 ("Сравни характеристики и соотношение цена/качество." if has_compare
                  else "Дай рекомендацию с обоснованием."),
        )


class CareerHRSkill(Skill):
    name = "career_hr"
    focus = "career_hr"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        topics = []
        if any(w in t for w in ("резюм", "cv", "curriculum")):
            topics.append("resume")
        if any(w in t for w in ("собеседован", "interview", "как пройт")):
            topics.append("interview")
        if any(w in t for w in ("увольн", "уйти", "quit", "уход")):
            topics.append("resignation")
        if any(w in t for w in ("повышен", "продвижен", "promot")):
            topics.append("promotion")
        if any(w in t for w in ("зарплат", "salary", "оклад", "зп")):
            topics.append("salary")
        if any(w in t for w in ("оффер", "offer", "предложен")):
            topics.append("job_offer")
        if any(w in t for w in ("карьер", "путь", "развити")):
            topics.append("career_path")
        skills_mentioned = _extract_nouns(user_text)
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "skills_mentioned": skills_mentioned[:8],
            },
            hint=f"Темы: {', '.join(topics) if topics else 'карьера'}. "
                 "Дай конкретные советы без общих фраз.",
        )


class HomeDIYSkill(Skill):
    name = "home_diy"
    focus = "home_diy"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        topics = []
        if any(w in t for w in ("ремонт", "ремонт", "шпакл", "штукатур", "обои", "покрас")):
            topics.append("renovation")
        if any(w in t for w in ("электрик", "розетк", "провод", "выключател")):
            topics.append("electrical")
        if any(w in t for w in ("сантехник", "труб", "кран", "унитаз", "раковин")):
            topics.append("plumbing")
        if any(w in t for w in ("мебел", "собр", "ике", "шкаф", "стол", "стул")):
            topics.append("furniture")
        if any(w in t for w in ("инструмент", "дрел", "шуруповерт", "перфоратор")):
            topics.append("tools")
        if any(w in t for w in ("дача", "огород", "сад", "газон", "теплиц")):
            topics.append("gardening")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "has_tools_question": "tools" in topics,
            },
            hint=f"Тема: {', '.join(topics) if topics else 'дом/DIY'}. "
                 "Дай пошаговую инструкцию с материалами и техникой безопасности.",
        )


class SportsFitnessSkill(Skill):
    name = "sports_fitness"
    focus = "sports_fitness"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        sports = []
        if any(w in t for w in ("бег", "run", "марафон")):
            sports.append("running")
        if any(w in t for w in ("тренаж", "гантел", "желез", "кач")):
            sports.append("gym")
        if any(w in t for w in ("йог", "yoga", "гибкост")):
            sports.append("yoga")
        if any(w in t for w in ("футбол", "football")):
            sports.append("football")
        if any(w in t for w in ("баскетбол", "basketball")):
            sports.append("basketball")
        if any(w in t for w in ("плаван", "swim")):
            sports.append("swimming")
        if any(w in t for w in ("велосипед", "bike")):
            sports.append("cycling")
        if any(w in t for w in ("бокс", "box")):
            sports.append("boxing")
        if any(w in t for w in ("кроссфит", "crossfit")):
            sports.append("crossfit")
        has_nutrition = any(w in t for w in ("питан", "диет", "белк", "калори", "bcaa"))
        has_beginner = any(w in t for w in ("начина", "нович", "beginner"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "sports": sports,
                "needs_nutrition_advice": has_nutrition,
                "beginner_mode": has_beginner,
            },
            hint=f"Вид спорта: {', '.join(sports) if sports else 'фитнес'}. "
                 "Дай программу тренировок с прогрессией и техникой безопасности.",
        )


class GamingSkill(Skill):
    name = "gaming"
    focus = "gaming"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        genres = []
        if any(w in t for w in ("rpg", "рогалик", "skyrim", "witcher", "вдов")):
            genres.append("rpg")
        if any(w in t for w in ("стратеги", "страт", "страте", "starcraft", "civ")):
            genres.append("strategy")
        if any(w in t for w in ("шутер", "shooter", "call of", "battlefield", "cs", "valorant")):
            genres.append("shooter")
        if any(w in t for w in ("инди", "indie", "stardew", "hollow")):
            genres.append("indie")
        if any(w in t for w in ("мобил", "mobile", "android game")):
            genres.append("mobile")
        has_guide = any(w in t for w in ("прохождени", "гайд", "guide", "walkthrough", "совет"))
        has_build = any(w in t for w in ("сборк", "build", "конфиг", "specs", "пк"))
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "genres": genres,
                "guide_requested": has_guide,
                "pc_build_question": has_build,
            },
            hint=f"Жанр: {', '.join(genres) if genres else 'игры'}. "
                 "Дай практичный ответ: стратегия, билд или прохождение.",
        )


class BusinessEntrepreneurSkill(Skill):
    name = "business_entrepreneur"
    focus = "business"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        topics = []
        if any(w in t for w in ("стартап", "startup", "бизнес иде", "launch")):
            topics.append("startup")
        if any(w in t for w in ("маркетин", "market", "продвижен")):
            topics.append("marketing_strategy")
        if any(w in t for w in ("юридическ", "оформ", "ип", "ооо", "регистрац")):
            topics.append("legal_entity")
        if any(w in t for w in ("налог", "tax", "бухгалтер")):
            topics.append("accounting")
        if any(w in t for w in ("кредит", "грант", "инвестиц", "funding")):
            topics.append("funding")
        if any(w in t for w in ("команд", "team", "найм", "hiring")):
            topics.append("team")
        if any(w in t for w in ("питч", "pitch", "презентац")):
            topics.append("pitch")
        numbers = re.findall(r"\d+(?:[.,]\d+)?", user_text or "")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "numbers": numbers[:6],
            },
            hint=f"Тема: {', '.join(topics) if topics else 'бизнес'}. "
                 "Дай конкретные шаги, цифры и референсы.",
        )


class CryptoInvestSkill(Skill):
    name = "crypto_invest"
    focus = "crypto_investments"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        assets = []
        if any(w in t for w in ("bitcoin", "btc", "биткоин")):
            assets.append("btc")
        if any(w in t for w in ("ethereum", "eth", "эфир")):
            assets.append("eth")
        if any(w in t for w in ("solana", "sol")):
            assets.append("sol")
        if any(w in t for w in ("акци", "stock", "share")):
            assets.append("stocks")
        if any(w in t for w in ("облигац", "bond")):
            assets.append("bonds")
        if any(w in t for w in ("депозит", "deposit", "вклад")):
            assets.append("deposits")
        if any(w in t for w in ("недвижимост", "real estate")):
            assets.append("real_estate")
        topics = []
        if any(w in t for w in ("купи", "buy", "вход")):
            topics.append("entry")
        if any(w in t for w in ("прода", "sell", "выход")):
            topics.append("exit")
        if any(w in t for w in ("анализ", "аналитик", "обзор", "прогноз")):
            topics.append("analysis")
        if any(w in t for w in ("хран", "storage", "wallet", "кошелек", "биржа")):
            topics.append("storage")
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "assets": assets,
                "topics": topics,
            },
            hint=f"Активы: {', '.join(assets) if assets else 'инвестиции'}. "
                 "Не давай инвестиционных советов. Объясни риски и механики.",
        )


class AutoVehicleSkill(Skill):
    name = "auto_vehicle"
    focus = "auto_vehicle"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        topics = []
        if any(w in t for w in ("выбор", "купи", "покупк", "какую машин", "recommend")):
            topics.append("choosing")
        if any(w in t for w in ("ремонт", "почин", "не работ", "стук", "гул")):
            topics.append("repair")
        if any(w in t for w in ("обслуж", "то", "масл", "фильтр", "замен")):
            topics.append("maintenance")
        if any(w in t for w in ("шин", "колес", "резин")):
            topics.append("tires")
        if any(w in t for w in ("страхов", "осаго", "каско")):
            topics.append("insurance")
        brands = re.findall(
            r"(?i)(toyota|honda|bmw|mercedes|audi|volkswagen|vw|renault|peugeot|citroen|"
            r"ford|chevrolet|nissan|hyundai|kia|mazda|subaru|lexus|skoda|opel|fiat|"
            r"lada|vaz|uaz|gaz|byd|tesla)",
            user_text or "",
        )
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "topics": topics,
                "brands": list(set(b.lower() for b in brands)),
            },
            hint=f"Марки: {', '.join(set(b.lower() for b in brands[:5])) if brands else 'не указаны'}. "
                 "Дай практичный ответ с учётом бюджета и условий эксплуатации.",
        )


class ShoppingDealsSkill(Skill):
    name = "shopping_deals"
    focus = "shopping"

    async def run(self, *, intent: str, user_text: str, context: Dict[str, Any],
                  user_facts: Dict[str, Any], digital_twin: Dict[str, Any]) -> SkillResult:
        t = (user_text or "").lower()
        has_price = any(w in t for w in ("цен", "price", "стоимост", "сколько"))
        has_compare = any(w in t for w in ("сравн", "vs", "или", "лучш"))
        has_discount = any(w in t for w in ("скидк", "акци", "sale", "распродаж"))
        has_review = any(w in t for w in ("отзыв", "review", "стоит ли"))
        product_keywords = _extract_nouns(user_text)
        return SkillResult(
            result={
                "skill": self.name,
                "focus": self.focus,
                "price_query": has_price,
                "comparison": has_compare,
                "looking_for_discount": has_discount,
                "needs_review": has_review,
                "product_terms": product_keywords[:10],
            },
            hint="Помоги с выбором: определи потребность -> критерии -> лучший вариант.",
        )


# ── Реестр ───────────────────────────────────────────────────────────────────

def default_skills() -> List[Skill]:
    return [
        TranslatorSkill(),
        TeacherSkill(),
        ProgrammerSkill(),
        FinanceHelperSkill(),
        TaskPlannerSkill(),
        ScheduleHelperSkill(),
        LearningHelperSkill(),
        CoachSkill(),
        LawyerSkill(),
        MarketerSkill(),
        AnalystSkill(),
        DevOpsAssistantSkill(),
        TelegramBotEngineerSkill(),
        PsychologistSoftSkill(),
        FinanceHelper2Skill(),
        NewsHelperSkill(),
        HealthHelperSkill(),
        RecipeCookingSkill(),
        MathReasoningSkill(),
        PhysicsEngineerSkill(),
        GeographyTravelSkill(),
        HistoryCultureSkill(),
        LiteratureArtSkill(),
        BiologyNatureSkill(),
        TechGadgetsSkill(),
        CareerHRSkill(),
        HomeDIYSkill(),
        SportsFitnessSkill(),
        GamingSkill(),
        BusinessEntrepreneurSkill(),
        CryptoInvestSkill(),
        AutoVehicleSkill(),
        ShoppingDealsSkill(),
    ]
