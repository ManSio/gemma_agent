from __future__ import annotations

from typing import Any, Dict


class GreetingsModule:
    def private_intro(self) -> str:
        return (
            "Привет! Я ассистент: чат, объяснения, планы, расчёты и напоминания.\n"
            "Пишите обычным языком — «переведи…», «посчитай…», «напомни в 22:50…». "
            "Справка: /help · забыть факт: /forget."
        )

    def group_intro(self) -> str:
        return "Привет всем! Я помогу с вопросами и задачами. Зовите по упоминанию или командой."
