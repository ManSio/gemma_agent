from __future__ import annotations

import re
from typing import Dict, Optional


def detect_skill_intent(text: str) -> Optional[str]:
    t = (text or "").lower()

    # translator
    if any(k in t for k in ("translate", "переведи", "перевод", "translation", "translat")):
        return "translator"
    if re.search(r"(?i)(?:по[-\s]?|на\s+)(?:английск|русск|немецк|французск|украинск|белорусск)", t):
        return "translator"
    if re.search(
        r"(?i)\b(?:english|german|french|russian|deutsch|français)\s*:\s*",
        t,
    ):
        return "translator"

    # teacher / education
    if any(k in t for k in ("объясни", "explain", "урок", "тема", "обучени", "науч",
                            "учебн", "понятн", "репетитор")):
        return "teacher"

    # programmer / code
    if any(k in t for k in ("bug", "debug", "код", "code", "ошибка", "lint", "refactor",
                            "патч", "patch", "рефактор", "функци", "алгоритм", "репозитор",
                            "класс", "файл", "import", "фреймвор")):
        return "programmer"

    # finance
    if any(k in t for k in ("бюджет", "финанс", "budget", "expense", "доход", "расход", "копилк")):
        return "finance_helper"

    # advanced finance (more complex queries)
    if any(k in t for k in ("диверсифи", "доходност", "forecast",
                            "фин модель", "финмодел", "финансовое модел")):
        return "finance_helper_2"

    # task planner
    if any(k in t for k in ("план", "plan", "цель", "goal", "задач", "сделать", "напомн")):
        return "task_planner"

    # schedule
    if any(k in t for k in ("расписание", "schedule", "день", "week", "график", "режим", "недел")):
        return "schedule_helper"

    # learning
    if any(k in t for k in ("учеб", "study", "повтор", "learning", "курс", "материал")):
        return "learning_helper"

    # coach / motivation
    if any(k in t for k in ("мотива", "coach", "поддерж", "совет", "самооценк")):
        return "coach"

    # legal
    if any(k in t for k in ("юрист", "закон", "legal", "право", "адвокат", "судеб",
                            "договор", "иск", "штраф", "наследств", "нотариус")):
        return "lawyer"

    # marketing
    if any(k in t for k in ("маркетинг", "реклам", "продвиж", "позиционирован", "анализ рынк",
                            "конкурент", "продаж", "конверси")):
        return "marketer"

    # analytics
    if any(k in t for k in ("анализ", "analytics", "статистик", "метрик", "dashboard",
                            "отчёт", "report", "данные", "показател")):
        return "analyst"

    # devops
    if any(k in t for k in ("devops", "деплой", "ci/cd", "docker", "kubernetes", "сервер",
                            "инфраструктур", "мониторинг", "terraform", "депло")):
        return "devops_assistant"

    # telegram bot engineering
    if any(k in t for k in ("telegram бот", "aiogram", "telegram bot", "бот телеграм")):
        return "telegram_bot_engineer"

    # psychology / support
    if any(k in t for k in ("психолог", "эмоци", "чувств", "стресс", "тревог", "депресси",
                            "одиночеств", "отношени")):
        return "psychologist_soft"

    # news
    if any(k in t for k in ("новост", "news", "сводк", "что произошл", "последн", "событ",
                            "дайджест", "свіж")):
        return "news_helper"

    # health
    if any(k in t for k in ("здоровь", "здоров", "health", "симптом", "бол", "лечен",
                            "таблетк", "врач", "терапевт", "болит")):
        return "health_helper"

    # cooking / recipes
    if any(k in t for k in ("рецепт", "recipe", "готов", "приготов", "блюд", "кулинар",
                            "варк", "жарк", "запекан")):
        return "recipe_cooking_helper"

    # ── Новые скиллы ──────────────────────────────────────────────────────

    # math reasoning
    if any(k in t for k in ("реши", "вычисли", "math", "математик", "уравнен", "пример",
                            "∑", "∫", "√", "π", "посчитай", "формул")):
        return "math_reasoning"

    # physics / engineering
    if any(k in t for k in ("физик", "physics", "механик", "электричеств", "теплот",
                            "оптик", "сопромат", "термодинам")):
        return "physics_engineer"

    # geography / travel
    if any(k in t for k in ("географи", "страна", "столиц", "путешеств", "поездк",
                            "туризм", "travel", "отдых")):
        return "geography_travel"

    # history / culture
    if any(k in t for k in ("истори", "history", "эпох", "древн", "средневеков",
                            "войн", "цивилизац")):
        return "history_culture"

    # literature / art
    if any(k in t for k in ("литератур", "книг", "роман", "поэт", "писател",
                            "art", "музык", "фильм", "кино")):
        return "literature_art"

    # biology / nature
    if any(k in t for k in ("биологи", "biology", "растен", "животн", "генетик",
                            "эколог", "природ")):
        return "biology_nature"

    # tech gadgets
    if any(k in t for k in ("смартфон", "ноутбук", "гаджет", "техник", "характеристик",
                            "gpu", "cpu", "наушник", "монитор")):
        return "tech_gadgets"

    # career / hr
    if any(k in t for k in ("карьер", "резюм", "cv", "собеседован", "ваканс",
                            "работа", "зарплат", "оффер")):
        return "career_hr"

    # home diy
    if any(k in t for k in ("ремонт", "сантехник", "электрик", "инструмент",
                            "diy", "сделай сам", "мебел")):
        return "home_diy"

    # sports / fitness
    if any(k in t for k in ("спорт", "sport", "тренировк", "фитнес", "fitness",
                            "упражнен", "бег", "качалк", "йог")):
        return "sports_fitness"

    # gaming
    if any(k in t for k in ("игр", "game", "гейминг", "gaming", "прохождени",
                            "steam", "плойк", "xbox", "rpg")):
        return "gaming"

    # business / entrepreneur
    if any(k in t for k in ("бизнес", "business", "стартап", "startup", "предпринимател",
                            "компани", "питч", "монетизац")):
        return "business_entrepreneur"

    # crypto / investments
    if any(k in t for k in ("биткоин", "bitcoin", "crypto", "крипт", "инвестиц",
                            "stock", "трейдинг", "портфел", "дивиденд", "бирж")):
        return "crypto_invest"
    if re.search(r"(?i)\bакци[йя]\b", t) and re.search(r"(?i)(бирж|stock|портфел|дивиденд|ipo)", t):
        return "crypto_invest"

    # auto / vehicle
    if any(k in t for k in ("авто", "машин", "car", "автомобил", "двигател", "шин",
                            "запчаст", "техосмотр", "дизел")):
        return "auto_vehicle"

    # shopping / deals
    if any(k in t for k in ("покупк", "shopping", "магазин", "скидк",
                            "распродаж", "дешев", "купон", "промокод", "чёрная пятница")):
        return "shopping_deals"

    return None


def skill_context_pack(context: Dict) -> Dict:
    sit = context.get("situation")
    return {
        "dialogue_state": context.get("dialogue_state", {}),
        "psychology": context.get("psychology", {}),
        "persona": context.get("persona", {}),
        "file_context": context.get("file_context", {}),
        "code_intake": context.get("code_intake", {}),
        "document_intake": context.get("document_intake", {}),
        "user_facts": context.get("user_facts", {}),
        "situation": sit if isinstance(sit, dict) else {},
    }
