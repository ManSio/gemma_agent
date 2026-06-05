"""
Предсказание ближайших шагов (горизонт без отдельного LLM-вызова).
Влияет только на промпт: goal_plan.lookahead + thinking_markers, не меняет роутинг.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from core.dialogue_feedback_signals import user_feedback_likely
from core.dialogue_plot_signals import plot_twist_likely
from core.module_gen_intent import plugin_programming_prefers_general
from core.runtime_telegram_settings import effective_bool


def enabled() -> bool:
    return effective_bool("LOOKAHEAD_PLANNER_ENABLED", default=True)


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _planner_mode(user_text: str, intent: str, planner_reason: str) -> str:
    low = (user_text or "").strip().lower()
    it = (intent or "").strip().lower()
    pr = (planner_reason or "").strip().lower()
    if it == "test" or "test" in low or "тест" in low or "f-series" in low or "s-series" in low:
        return "TEST_MODE"
    if it in {"reasoning", "logic"} or "δ" in low or "дельта" in low or "рассужд" in low:
        return "REASONING_MODE"
    if it in {"explain", "teacher"} or "объясни" in low or "поясни" in low or "explain" in low:
        return "EXPLAIN_MODE"
    # Short acknowledgements/noise should preserve current context and not derail the chain.
    if (
        it == "general"
        and len(low) <= 24
        and (
            low in {"ок", "окей", "ага", "угу", "ясно", "понял", "принял", "go", "да", "нет"}
            or re.fullmatch(r"[\W_]+", low or "") is not None
        )
    ) or "noise" in pr:
        return "NOISE_MODE"
    return "DEFAULT_MODE"


def build_lookahead_plan(
    *,
    user_text: str,
    intent: str,
    module: str,
    planner_reason: str,
    fallback: bool,
    goal_hints: Dict[str, Any],
    predictive_hint: Dict[str, Any],
    knowledge_hint: Dict[str, Any],
    skill_name: str,
) -> Dict[str, Any]:
    if not enabled():
        return {}
    text = (user_text or "").strip()
    low = text.lower()
    mod = (module or "").replace("-", "_").lower()
    kh_pol = str((knowledge_hint or {}).get("policy") or "")
    planner_mode = _planner_mode(text, intent, planner_reason)

    steps: List[Dict[str, str]] = []
    verify: List[str] = []
    followups: List[str] = []

    def add_step(do: str, why: str) -> None:
        steps.append({"do": _clip(do, 220), "why": _clip(why, 160)})

    if planner_mode == "TEST_MODE":
        add_step(
            "Определить формат теста (критерии, ожидаемый вывод, формат ответа) и держаться его до конца.",
            "Тестовый режим ломается, когда формат плавает между сообщениями.",
        )
        add_step(
            "Сначала прогнать проверку по пунктам, затем вернуть результат в виде test-report (pass/fail + краткая причина).",
            "Нужна воспроизводимость и сравнимость серий.",
        )
        verify.append("Ответ строго в формате теста, без лишней смены роли/тона.")
        followups.append("Запустить следующий кейс тем же форматом.")
    elif planner_mode == "REASONING_MODE":
        add_step(
            "Продолжить математическую/логическую цепочку из текущего контекста, не обнуляя предыдущие допущения.",
            "Иначе происходит дрейф и потеря линии доказательства.",
        )
        add_step(
            "Явно отметить переходы между шагами рассуждения и проверку ограничений.",
            "Повышает стабильность в сложных цепочках.",
        )
        verify.append("Сохраняется согласованность с уже выведенными условиями.")
        followups.append("Проверка следующей ветки рассуждения при тех же ограничениях.")
    elif planner_mode == "EXPLAIN_MODE":
        add_step(
            "Подстроить стиль под объяснение: сначала простая версия, затем при необходимости углубление.",
            "Пользователь просит именно объяснение, а не сухой итог.",
        )
        add_step(
            "Сохранить контекст вопроса и дать 1 короткий пример для закрепления.",
            "Уменьшает риск недопонимания без перегруза.",
        )
        verify.append("Стиль и глубина соответствуют explain/teacher запросу.")
        followups.append("Уточнение, нужно ли более детально или короче.")
    elif planner_mode == "NOISE_MODE":
        add_step(
            "Не менять рабочий контекст и не переключать сценарий; считать реплику служебной/подтверждающей.",
            "Короткие шумовые реплики не должны ломать ход решения.",
        )
        add_step(
            "Продолжить текущую линию ответа или ждать следующего содержательного шага.",
            "Сохраняется устойчивость диалога.",
        )
        verify.append("Контекст и активная линия не сброшены.")
        followups.append("Запрос следующего содержательного шага без смены темы.")

    # --- Плагин / self-programming ---
    else:
        try:
            plugin_ctx = plugin_programming_prefers_general(text)
        except Exception:
            plugin_ctx = False
        if plugin_ctx or "module.json" in low or ("execute" in low and "плагин" in low):
            add_step(
                "Уточнить у пользователя имя плагина, входы/выходы и одну ключевую команду.",
                "Без контракта легко сгенерировать несовместимый модуль.",
            )
            add_step(
                "Сформировать или поправить manifest + entrypoint; проверить capabilities/commands.",
                "Маршрутизация зависит от манифеста.",
            )
            add_step(
                "Предложить тестовую команду и при необходимости hot_install.",
                "Закрыть цикл «код → в реестре → проверка».",
            )
            verify.append("Ответ не обещает лишнего, чего нет в SelfProgramming/tools.")
            followups.append("Запуск тестовой команды и вывод ошибки, если что-то падает.")
            followups.append("Добавить capabilities или уточнить intent под сценарий.")

        # --- Math ---
        elif intent == "math" or re.search(r"^\s*/calc\b", low):
            add_step("Безопасно вычислить или вызвать калькулятор; показать результат и единицы.", "Явный числовой исход.")
            add_step("Кратко проверить порядок величины / знак, если выражение сложное.", "Снизить риск опечатки в формуле.")
            verify.append("Если пользователь смешал текст и формулу — не игнорировать текстовую часть кратко.")
            followups.append("Упростить выражение или другой пример.")
            followups.append("Объяснить ход вычисления словами.")

        # --- Поправка к прошлому ответу (обратная связь) ---
        elif user_feedback_likely(text):
            add_step(
                "Считать последнюю реплику приоритетной над предыдущим планом ответа; снять навязанный сценарий, если пользователь его отвергает.",
                "Иначе бот «липнет» к плану и игнорирует замечание.",
            )
            add_step(
                "Кратко признать сдвиг или переспросить один раз, если без этого нельзя исправить ответ.",
                "Меньше оправданий, больше исправления по сути.",
            )
            add_step(
                "Не запускать цепочку инструментов «как в прошлый раз», если пользователь указал, что направление неверно — переоцени, нужен ли TOOL_CALL.",
                "Экономия шагов и доверие.",
            )
            verify.append("Ответ адресует замечание, а не продолжает прежнюю линию вслепую.")
            followups.append("Проверка, что исправление совпало с ожиданием.")
            followups.append("Следующий шаг по теме после согласования.")

        # --- Резкий поворот сюжета / отношений (ролевой диалог, коучинг) ---
        elif plot_twist_likely(text):
            add_step(
                "Принять последнюю реплику пользователя как новый канон; не продолжать предыдущую линию, если она ей противоречит.",
                "Иначе модель «залипает» на старом сценарии.",
            )
            add_step(
                "Коротко отразить эмоцию или факт поворота; один уточняющий вопрос только если без него нельзя ответить по сути.",
                "Меньше растерянности и общих фраз после сильного события.",
            )
            add_step(
                "Не предсказывать дальнейшие шаги из старого контекста; предложить 1–2 реалистичных варианта «что дальше» уже из нового состояния.",
                "Lookahead должен опираться на свежий факт.",
            )
            verify.append(
                "Нет возврата к устаревшим допущениям (пара всё ещё вместе и т.п.), если пользователь явно их отменил."
            )
            followups.append("Детали решения пользователя или границы, что важно сохранить.")
            followups.append("Поддержка или следующий маленький шаг без морализаторства.")

        # --- Код / программистский bias ---
        elif intent == "general" and (
            "programmer" in (predictive_hint.get("skill_priority") or [])
            or any(k in low for k in ("def ", "class ", "traceback", "import ", "ошибка", "exception"))
        ):
            add_step("Сформулировать предположение о цели и среде (версия Python, фреймворк).", "Меньше неверных допущений.")
            add_step("Дать минимальный исправленный фрагмент или шаг отладки.", "Практическая польза сразу.")
            add_step("Назвать один проверочный шаг (запуск теста, лог, print).", "Закрепить исправление.")
            verify.append("Не выдумывать пути к файлам и версии пакетов без данных.")
            followups.append("Полный traceback или MCVE.")
            followups.append("Рефакторинг или тесты.")

        # --- Погода / валюта / время (skill или knowledge) ---
        elif skill_name in ("weather", "currency") or kh_pol or any(
            k in low for k in ("погод", "курс", "валют", "время", "timezone")
        ):
            add_step("Подставить факты из API/hint; дать точные данные и краткий контекст применения.", "Пользователь ждёт проверяемые данные.")
            add_step("Если город/валюта неясны — один уточняющий вопрос вместо длинного ответа.", "Меньше неверных геокодов.")
            verify.append("Согласовать единицы (°C/°F, TZ) с профилем пользователя, если известно.")
            followups.append("Прогноз на другой день или другой город.")
            followups.append("Связанный финансовый/временной вопрос.")

        # --- Диалог при «запасном» роутинге планировщика ---
        elif (planner_reason or "").startswith("chat_orchestrator_fallback") or fallback:
            add_step("Определить, чего не хватило: команда, вложение, или общий вопрос.", "Часто нет модуля под узкий intent.")
            add_step("Дать полезный ответ в чате или предложить одну конкретную команду.", "Не оставлять пустым разрывом.")
            verify.append("Не выдумывать несуществующие slash-команды.")
            followups.append("Уточнение темы одним предложением.")
            followups.append("Загрузка файла или использование /help.")

        # --- Универсальный горизонт ---
        else:
            gid = goal_hints.get("goal_ids") or []
            if isinstance(gid, list) and gid:
                add_step(
                    f"Согласовать ответ с активными целями ({', '.join(str(x) for x in gid[:3])}).",
                    "Долгие цели из goal_hints.",
                )
            add_step("Ответить по сути текущего сообщения.", "Основной пользовательский запрос.")
            add_step(
                "Оценить, достаточно ли данных; при нехватке — один короткий вопрос.",
                "Снижает циклы уточнений.",
            )
            verify.append("Итог не противоречит routing_prefs и facts из контекста.")
            active_goals = goal_hints.get("active_goals") or []
            if isinstance(active_goals, list) and active_goals:
                followups.append("Следующий шаг к цели из переписки.")
            followups.append("Углубление или пример.")

    try:
        max_steps = max(2, min(5, int((os.getenv("LOOKAHEAD_MAX_STEPS") or "4").strip() or "4")))
    except ValueError:
        max_steps = 4
    steps = steps[:max_steps]

    try:
        horizon = max(2, min(5, int((os.getenv("LOOKAHEAD_HORIZON") or "3").strip() or "3")))
    except ValueError:
        horizon = 3

    return {
        "version": "1",
        "horizon": horizon,
        "intent": intent,
        "module": mod,
        "planner_mode": planner_mode,
        "steps": steps,
        "after_response_verify": verify[:4],
        "likely_followups": followups[:4],
        "signals": {
            "skill_name": skill_name or "",
            "planner_reason_head": _clip(planner_reason, 80),
        },
    }
