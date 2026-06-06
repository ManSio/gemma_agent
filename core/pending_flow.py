"""
Единый контур прерывания pending-сценариев.

Когда у пользователя есть «висящие» состояния (math clarify, image multi-step,
document drafts и т.д.), и он пишет короткое отрицание/стоп («нет», «отмена»,
«стоп», «cancel»), мы не должны:
- интерпретировать это как отдельную задачу для модели,
- крутить fallback-петлю «не понял — а вот команды — нет — Понял...».

Вместо этого мы:
1. Чистим все pending-сценарии для (user_id, chat_id).
2. Возвращаем короткий ответ-подтверждение прерывания.
3. Прерываем дальнейшую маршрутизацию.

Регистрация чистильщиков:
- модули/слои регистрируют `(name, clear_fn)` через `register_pending_source`;
- `clear_fn(user_id, chat_id) -> bool` — True, если что-то реально очистили.

Список «отрицаний» жёсткий — только нормализованные токены, чтобы не задеть
обычные сообщения вроде «нет, давай попробуем по-другому» (они длиннее
порога и считаются содержательным сообщением).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ClearFn = Callable[[str, str], bool]

_NEGATIVE_TOKENS = {
    "нет",
    "не",
    "стоп",
    "отмена",
    "отмени",
    "хватит",
    "забудь",
    "no",
    "stop",
    "cancel",
    "abort",
}

_MAX_INTERRUPT_LEN = 24

_REGISTRY: Dict[str, ClearFn] = {}


def register_pending_source(name: str, clear_fn: ClearFn) -> None:
    """Регистрирует источник pending-состояния. Идемпотентно по имени."""
    if not name or not callable(clear_fn):
        return
    _REGISTRY[name] = clear_fn


def is_negative_interrupt(text: str) -> bool:
    """True если текст — короткое отрицание/стоп/отмена."""
    if not text:
        return False
    raw = text.strip().lower()
    if not raw:
        return False
    if len(raw) > _MAX_INTERRUPT_LEN:
        return False
    norm = raw.rstrip("!?.,…")
    norm = norm.replace("ё", "е")
    if norm in _NEGATIVE_TOKENS:
        return True
    parts = norm.split()
    if len(parts) <= 2 and parts and parts[0] in _NEGATIVE_TOKENS:
        return True
    return False


def clear_all_pending(user_id: str, chat_id: str) -> List[str]:
    """Сносит все известные pending для пары (user, chat). Возвращает имена реально очищенных."""
    cleared: List[str] = []
    for name, fn in list(_REGISTRY.items()):
        try:
            if fn(str(user_id), str(chat_id)):
                cleared.append(name)
        except Exception as exc:
            logger.debug("pending_flow: clear_fn '%s' failed: %s", name, exc)
    return cleared


def try_handle_negative_interrupt(
    *, text: str, user_id: str, chat_id: str
) -> Optional[Tuple[str, List[str]]]:
    """
    Если text — короткое отрицание/стоп И есть хотя бы один реальный pending,
    очистить всё и вернуть (response_text, cleared_names).

    Если pending не было или text не отрицание — None (даём обычной маршрутизации
    сделать своё дело).
    """
    if not is_negative_interrupt(text):
        return None
    cleared = clear_all_pending(user_id, chat_id)
    if not cleared:
        return None
    if len(cleared) == 1:
        msg = "Понял, отменил отложенный вопрос. Что дальше?"
    else:
        msg = "Понял, отменил все отложенные вопросы. Что дальше?"
    return msg, cleared


def has_any_pending(user_id: str, chat_id: str) -> bool:
    """Эвристика: есть ли что-то ожидающее ответа.

    Реализуется через peek-функции, если модуль их зарегистрировал, иначе
    через попытку clear() — но без фактической очистки.
    Чтобы не плодить отдельный API, мы просто пытаемся вызвать clear на тестовый
    ключ — это не годится. Проще — потребитель регистрирует peek_fn.
    Пока возвращаем True если зарегистрирован хоть один источник: это безопасно
    для use-case'а (мы не очищаем сами, просто подсказываем UI).
    """
    return bool(_REGISTRY)


def list_registered_sources() -> List[str]:
    return list(_REGISTRY.keys())


__all__ = [
    "register_pending_source",
    "is_negative_interrupt",
    "clear_all_pending",
    "try_handle_negative_interrupt",
    "has_any_pending",
    "list_registered_sources",
]
