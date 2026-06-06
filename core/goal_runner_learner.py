"""
GoalRunnerLearner — самообучающийся фильтр для Goal Runner.

Читает исходы (timeout / done_ok / skip_ok / done_fail / unnecessary)
и учится не запускать Goal Runner для текстов, где он не нужен.

Хранилище: JSONL (data/runtime/goal_runner_learner.jsonl)
Логика: если текст похож на ранее "провальные" или "бесполезные" — skip.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_WINDOW_SIZE = 200  # сколько последних записей учитывать
_SKIP_IF_TIMEOUT_RATIO = 0.3  # если >30% похожих текстов ушли в timeout → skip
_SKIP_IF_UNNECESSARY_COUNT = 2  # если >=2 похожих unnecessary → skip
_TEXT_HASH_LEN = 40  # длина хэша для fuzzy-сравнения (первые N символов)

_LEARNER_PATH = "data/runtime/goal_runner_learner.jsonl"


def _learner_enabled() -> bool:
    """GOAL_RUNNER_LEARNER_ENABLED: по умолчанию true (вкл)."""
    raw = os.getenv("GOAL_RUNNER_LEARNER_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _learner_path() -> Path:
    p = Path(os.getenv("GOAL_RUNNER_LEARNER_PATH", _LEARNER_PATH))
    if not p.is_absolute():
        root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
        p = Path(root) / p
    return p.resolve()


def _text_sig(text: str) -> str:
    """Сигнатура текста: нормализованные первые 100 символов."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return hashlib.md5(t[:100].encode("utf-8")).hexdigest()


def _text_prefix(text: str) -> str:
    """Префикс текста для fuzzy-сравнения: первые N символов."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t[:_TEXT_HASH_LEN]


class GoalRunnerLearner:
    """
    Учится на исходах Goal Runner.
    Thread-safe, read-heavy, append-only JSONL.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: Deque[Dict[str, Any]] = deque(maxlen=_WINDOW_SIZE)
        self._loaded = False
        self._timeout_count = 0
        self._total_count = 0
        # Кэш: сигнатура → количество плохих исходов
        self._bad_sigs: Dict[str, int] = defaultdict(int)
        self._unnecessary_sigs: Dict[str, int] = defaultdict(int)

    # ─── Загрузка ────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = _learner_path()
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                self._records.append(rec)
                self._ingest_record(rec)
        except OSError as e:
            logger.debug("[GoalRunnerLearner] load error: %s", e)

    def _ingest_record(self, rec: Dict[str, Any]) -> None:
        self._total_count += 1
        outcome = str(rec.get("outcome") or "")
        sig = str(rec.get("text_sig") or "")
        if outcome in ("timeout", "done_fail"):
            self._timeout_count += 1
            if sig:
                self._bad_sigs[sig] += 1
        if outcome == "unnecessary":
            if sig:
                self._unnecessary_sigs[sig] += 1

    # ─── Публичные методы ────────────────────────────────────────────────

    def should_skip(self, user_text: str) -> bool:
        """
        True → не запускать Goal Runner, отдать обычному пайплайну.
        Решение на основе истории.
        """
        if not _learner_enabled():
            return False
        self._ensure_loaded()
        if not (user_text or "").strip():
            return False
        sig = _text_sig(user_text)
        prefix = _text_prefix(user_text)

        # 1. Точное совпадение сигнатуры — было unnecessary
        if self._unnecessary_sigs.get(sig, 0) >= _SKIP_IF_UNNECESSARY_COUNT:
            logger.debug("[GoalRunnerLearner] skip: exact sig %s unnecessary×%d", sig[:8], self._unnecessary_sigs[sig])
            return True

        # 2. Точное совпадение — высокий rate timeout
        bad_count = self._bad_sigs.get(sig, 0)
        total_for_sig = self._records_count_for_sig(sig)
        if total_for_sig >= 2 and bad_count / total_for_sig > _SKIP_IF_TIMEOUT_RATIO:
            logger.debug(
                "[GoalRunnerLearner] skip: sig %s timeout ratio %.0f%%",
                sig[:8], bad_count / total_for_sig * 100,
            )
            return True

        # 3. Fuzzy: похожий префикс был unnecessary
        fuzzy_unnecessary = self._fuzzy_prefix_unnecessary_count(prefix)
        if fuzzy_unnecessary >= _SKIP_IF_UNNECESSARY_COUNT:
            logger.debug("[GoalRunnerLearner] skip: fuzzy prefix unnecessary×%d", fuzzy_unnecessary)
            return True

        return False

    def record_outcome(
        self,
        user_text: str,
        outcome: str,
        *,
        duration_s: float = 0.0,
        had_tools: bool = False,
        tool_count: int = 0,
        error: str = "",
    ) -> None:
        """
        Записать исход запуска Goal Runner.

        outcome: skip_ok | timeout | done_ok | done_fail | unnecessary | cancelled
        """
        text = (user_text or "").strip()
        if not text:
            return
        rec = {
            "ts": time.time(),
            "text_sig": _text_sig(text),
            "text_prefix": _text_prefix(text),
            "text_len": len(text),
            "outcome": outcome,
            "duration_s": round(duration_s, 1),
            "had_tools": bool(had_tools),
            "tool_count": int(tool_count),
            "error": (error or "")[:200],
        }
        with self._lock:
            self._records.append(rec)
            self._ingest_record(rec)
        # Асинхронная запись в JSONL
        try:
            path = _learner_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.debug("[GoalRunnerLearner] write error: %s", e)

    def snapshot(self) -> Dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            return {
                "enabled": _learner_enabled(),
                "total_records": self._total_count,
                "timeout_count": self._timeout_count,
                "bad_sigs_count": len(self._bad_sigs),
                "unnecessary_sigs_count": len(self._unnecessary_sigs),
                "window_size": _WINDOW_SIZE,
                "skip_if_timeout_ratio": _SKIP_IF_TIMEOUT_RATIO,
                "skip_if_unnecessary_count": _SKIP_IF_UNNECESSARY_COUNT,
                "recent_outcomes": [r.get("outcome") for r in list(self._records)[-20:]],
            }

    # ─── Внутренние методы ───────────────────────────────────────────────

    def _records_count_for_sig(self, sig: str) -> int:
        """Сколько записей с такой сигнатурой."""
        with self._lock:
            return sum(1 for r in self._records if r.get("text_sig") == sig)

    def _fuzzy_prefix_unnecessary_count(self, prefix: str) -> int:
        """Сколько unnecessary записей с таким же префиксом (первые N символов)."""
        if not prefix:
            return 0
        with self._lock:
            count = 0
            for r in self._records:
                if r.get("outcome") == "unnecessary" and r.get("text_prefix") == prefix:
                    count += 1
            return count

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._bad_sigs.clear()
            self._unnecessary_sigs.clear()
            self._timeout_count = 0
            self._total_count = 0
            self._loaded = False
        path = _learner_path()
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


# ─── Глобальный инстанс ─────────────────────────────────────────────────

_LEARNER: Optional[GoalRunnerLearner] = None


def get_goal_runner_learner() -> GoalRunnerLearner:
    global _LEARNER
    if _LEARNER is None:
        _LEARNER = GoalRunnerLearner()
    return _LEARNER


__all__ = [
    "GoalRunnerLearner",
    "get_goal_runner_learner",
]
