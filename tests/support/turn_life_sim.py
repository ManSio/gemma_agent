"""Симулятор «жизни» чата для hot-path тестов: случайные эпизоды, не фиксированный шаблон."""
from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_CITIES = (
    "Минск",
    "Москва",
    "Санкт-Петербург",
    "Варшава",
    "Алматы",
    "Тбилиси",
    "Ереван",
    "Киев",
    "Рига",
    "Таллин",
)
_TOPICS = (
    "ИИ",
    "криптовалюты",
    "космос",
    "медицина",
    "климат",
    "робототехника",
    "нейросети",
    "энергетика",
    "биотех",
    "квантовые компьютеры",
)
_STAY_PHRASES = ("ещё", "продолжай", "дальше", "и что дальше", "разверни подробнее")
_CORRECTION_PHRASES = (
    "не то",
    "ты не понял",
    "wrong",
    "я про другое",
    "это не то что я спрашивал",
)
_GREETINGS = ("привет", "здарова", "добрый день", "хай", "ку")
_DEEP_VERBS = ("сделай глубокий анализ", "разбери подробно", "исследуй тему", "составь план")
_GENERAL_OPEN = ("объясни", "расскажи про", "что такое", "как работает")

_KIND_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("weather", 0.14),
    ("math", 0.10),
    ("news", 0.10),
    ("general", 0.18),
    ("deep", 0.10),
    ("greet", 0.08),
    ("stay", 0.12),
    ("correction", 0.08),
    ("empty", 0.04),
    ("stale", 0.06),
)

_DIRECT_MOCKS: Dict[str, str] = {
    "weather": "core.weather_reply.try_weather_reply_sync",
    "math": "core.referential_math_reply.try_referential_math_reply_sync",
    "news": "core.news_reply.try_news_reply_sync",
}


def episode_seed(label: str) -> int:
    """Детерминированный seed из label; TURN_LIFE_SIM_SEED переопределяет для replay."""
    raw = (os.environ.get("TURN_LIFE_SIM_SEED") or "").strip()
    if raw.isdigit():
        return int(raw)
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


@dataclass
class SimTurn:
    """Один ход в эпизоде симуляции."""

    kind: str
    user_text: str
    mock_reply: Optional[str] = None
    inject_stale: bool = False
    finalize_override: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class SimEpisode:
    """Случайный многоходовый эпизод."""

    seed: int
    user_id: str
    turns: List[SimTurn]


class TurnLifeSimulator:
    """Генератор непредсказуемых (но воспроизводимых по seed) диалоговых эпизодов."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed

    def _pick_weighted_kind(self, *, has_history: bool) -> str:
        """Выбрать тип хода с учётом контекста (stay/correction только после истории)."""
        pool: List[Tuple[str, float]] = []
        for kind, w in _KIND_WEIGHTS:
            if kind in ("stay", "correction") and not has_history:
                continue
            pool.append((kind, w))
        total = sum(w for _, w in pool)
        r = self.rng.random() * total
        acc = 0.0
        for kind, w in pool:
            acc += w
            if r <= acc:
                return kind
        return pool[-1][0]

    def _weather_turn(self) -> SimTurn:
        """Случайный запрос погоды."""
        city = self.rng.choice(_CITIES)
        templates = (
            f"какая погода в {city}",
            f"погода {city} сейчас",
            f"сколько градусов в {city}",
            f"будет дождь в {city}?",
        )
        text = self.rng.choice(templates)
        temp = self.rng.randint(-15, 35)
        reply = f"В {city} сейчас {temp}°C, {self.rng.choice(('ясно', 'облачно', 'дождь', 'снег'))}."
        return SimTurn("weather", text, mock_reply=reply, tags=("direct", "fact"))

    def _math_turn(self) -> SimTurn:
        """Случайная арифметика."""
        a, b = self.rng.randint(1, 99), self.rng.randint(1, 99)
        op = self.rng.choice(("+", "-", "*"))
        if op == "+":
            ans = str(a + b)
        elif op == "-":
            ans = str(a - b)
        else:
            ans = str(a * b)
        templates = (
            f"сколько будет {a}{op}{b}",
            f"посчитай {a} {op} {b}",
            f"{a}{op}{b}=?",
        )
        return SimTurn("math", self.rng.choice(templates), mock_reply=ans, tags=("direct", "fact"))

    def _news_turn(self) -> SimTurn:
        """Случайный news-direct."""
        topic = self.rng.choice(_TOPICS)
        text = self.rng.choice(
            (
                f"какие новости про {topic}",
                f"что нового в мире про {topic}",
                "главные новости сегодня",
            )
        )
        n = self.rng.randint(2, 4)
        lines = [f"{i}. {self.rng.choice(_TOPICS)}: краткая сводка" for i in range(1, n + 1)]
        return SimTurn("news", text, mock_reply="\n".join(lines), tags=("direct", "fact"))

    def _general_turn(self) -> SimTurn:
        """Общий вопрос без direct shortcut."""
        topic = self.rng.choice(_TOPICS)
        text = f"{self.rng.choice(_GENERAL_OPEN)} {topic} простыми словами"
        return SimTurn("general", text, tags=("brain_fallback",))

    def _deep_turn(self) -> SimTurn:
        """DEEP lane без shortcut."""
        topic = self.rng.choice(_TOPICS)
        text = f"{self.rng.choice(_DEEP_VERBS)} {topic}"
        return SimTurn("deep", text, tags=("brain_fallback", "deep"))

    def _greet_turn(self) -> SimTurn:
        """Короткое приветствие."""
        return SimTurn("greet", self.rng.choice(_GREETINGS), tags=("chitchat",))

    def _stay_turn(self) -> SimTurn:
        """Follow-up stay в существующем треде."""
        return SimTurn("stay", self.rng.choice(_STAY_PHRASES), tags=("thread", "stay"))

    def _correction_turn(self) -> SimTurn:
        """Коррекция пользователя."""
        return SimTurn(
            "correction",
            self.rng.choice(_CORRECTION_PHRASES),
            tags=("thread", "correct"),
        )

    def _empty_turn(self) -> SimTurn:
        """Пустой payload."""
        return SimTurn("empty", self.rng.choice(("   ", "\n\t", "  \n  ")), tags=("empty",))

    def _stale_turn(self) -> SimTurn:
        """Ход с устаревшим generation token."""
        topic = self.rng.choice(_TOPICS)
        text = f"привет, расскажи про {topic}"
        return SimTurn("stale", text, inject_stale=True, tags=("stale",))

    def generate_turn(self, *, has_history: bool) -> SimTurn:
        """Сгенерировать один случайный ход."""
        kind = self._pick_weighted_kind(has_history=has_history)
        builders: Dict[str, Callable[[], SimTurn]] = {
            "weather": self._weather_turn,
            "math": self._math_turn,
            "news": self._news_turn,
            "general": self._general_turn,
            "deep": self._deep_turn,
            "greet": self._greet_turn,
            "stay": self._stay_turn,
            "correction": self._correction_turn,
            "empty": self._empty_turn,
            "stale": self._stale_turn,
        }
        return builders[kind]()

    def generate_episode(
        self,
        *,
        min_turns: int = 5,
        max_turns: int = 14,
        user_id: str = "",
    ) -> SimEpisode:
        """Случайный многоходовый эпизод; длина и порядок ходов не фиксированы."""
        n = self.rng.randint(min_turns, max_turns)
        uid = user_id or f"life_{self.seed & 0xFFFFFF:06x}"
        turns: List[SimTurn] = []
        has_history = False
        for _ in range(n):
            turn = self.generate_turn(has_history=has_history)
            turns.append(turn)
            if turn.kind not in ("empty", "stale"):
                has_history = True
        return SimEpisode(seed=self.seed, user_id=uid, turns=turns)


def direct_mock_targets() -> Sequence[str]:
    """Пути patch для direct shortcuts."""
    return tuple(_DIRECT_MOCKS.values())


def mock_reply_for_turn(turn: SimTurn) -> Optional[str]:
    """Ответ mock-а для direct shortcut или None."""
    return turn.mock_reply if turn.kind in _DIRECT_MOCKS else None


def episode_summary(episode: SimEpisode) -> str:
    """Краткая сводка эпизода для сообщений assert."""
    kinds = "→".join(t.kind[0] for t in episode.turns)
    return f"seed={episode.seed} user={episode.user_id} path={kinds}"
