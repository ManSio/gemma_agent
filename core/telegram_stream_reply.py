"""
Потоковая отдача ответа в Telegram (edit_message) + отмена по кнопке «Стоп».

Включается TELEGRAM_STREAM_REPLY_ENABLED (default false).
Привязка к progress-сообщению: input_layer зовёт telegram_stream_bind_progress после arm.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

STOP_PREFIX = "gs:stop:"
_Tuple4 = Optional[Tuple[Any, int, int, str]]
_bound: contextvars.ContextVar[_Tuple4] = contextvars.ContextVar("telegram_stream_bound", default=None)
_delivered: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("telegram_stream_delivered", default=None)
_cancelled: contextvars.ContextVar[bool] = contextvars.ContextVar("telegram_stream_cancelled", default=False)
_editing: contextvars.ContextVar[bool] = contextvars.ContextVar("telegram_stream_editing", default=False)

_chat_cancel: Dict[str, asyncio.Event] = {}
_chat_cancel_guard = asyncio.Lock()


def telegram_stream_reply_enabled() -> bool:
    raw = (os.getenv("TELEGRAM_STREAM_REPLY_ENABLED") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def telegram_stream_private_only() -> bool:
    raw = (os.getenv("TELEGRAM_STREAM_PRIVATE_ONLY") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def telegram_stream_eligible(*, is_private: bool) -> bool:
    if not telegram_stream_reply_enabled():
        return False
    if telegram_stream_private_only() and not is_private:
        return False
    return True


def telegram_stream_direct_only() -> bool:
    """True (default): stream только когда ход пойдёт в brain_direct_dialog."""
    raw = (os.getenv("TELEGRAM_STREAM_DIRECT_ONLY") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def telegram_stream_editing_active() -> bool:
    """Пока идёт SSE → edit progress: не трогать то же сообщение из telegram_progress."""
    return bool(_editing.get())


def telegram_stream_direct_turn_likely(user_text: str) -> bool:
    """Лёгкий предикат до execute_plan — совпадает с is_direct_dialog_eligible (без recent)."""
    try:
        from core.brain.dialogue_lane import is_direct_dialog_eligible

        return is_direct_dialog_eligible(
            user_text,
            brain_profile="standard",
            has_document_intake=False,
            has_file_context=False,
            recent_dialogue=None,
        )
    except Exception as e:
        logger.debug("telegram_stream_direct_turn_likely: %s", e)
        return False


def telegram_stream_should_bind(
    *,
    user_text: str,
    is_group: bool,
    user_id: str = "",
    is_admin: bool = False,
) -> bool:
    if not telegram_stream_eligible(is_private=not is_group):
        return False
    from core.telegram_stream_reasoning import admin_stream_reasoning_effective

    if is_admin and admin_stream_reasoning_effective(is_admin=True):
        return True
    if telegram_stream_direct_only():
        return telegram_stream_direct_turn_likely(user_text)
    return True


def _stream_min_edit_interval_sec() -> float:
    try:
        ms = float((os.getenv("TELEGRAM_STREAM_MIN_EDIT_INTERVAL_MS") or "900").strip())
    except ValueError:
        ms = 900.0
    return max(0.35, min(ms / 1000.0, 5.0))


def _stream_min_chars_before_edit() -> int:
    try:
        v = int((os.getenv("TELEGRAM_STREAM_MIN_CHARS") or "20").strip())
    except ValueError:
        v = 20
    return max(0, min(v, 200))


def build_stop_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏹ Стоп", callback_data=f"{STOP_PREFIX}{int(chat_id)}")]
        ]
    )


async def register_chat_cancel(chat_id: str) -> asyncio.Event:
    """Новый ход: отменяет предыдущий незавершённый stream в этом чате."""
    ev = asyncio.Event()
    async with _chat_cancel_guard:
        prev = _chat_cancel.get(chat_id)
        if prev is not None and not prev.is_set():
            prev.set()
        _chat_cancel[chat_id] = ev
    return ev


def get_chat_cancel_event(chat_id: str) -> Optional[asyncio.Event]:
    return _chat_cancel.get(str(chat_id))


async def request_chat_cancel(chat_id: str) -> bool:
    ev = _chat_cancel.get(str(chat_id))
    if ev is None or ev.is_set():
        return False
    ev.set()
    return True


async def clear_chat_cancel(chat_id: str) -> None:
    async with _chat_cancel_guard:
        _chat_cancel.pop(str(chat_id), None)


def telegram_stream_get_bound() -> _Tuple4:
    return _bound.get()


def telegram_stream_bind_progress(bot: Any, chat_id: int, message_id: int, user_id: str) -> None:
    _bound.set((bot, int(chat_id), int(message_id), str(user_id)))
    _delivered.set(None)
    _cancelled.set(False)


def telegram_stream_disarm() -> None:
    _bound.set(None)


def telegram_stream_has_delivery() -> bool:
    return _delivered.get() is not None


def telegram_stream_take_delivery() -> Optional[str]:
    """Текст, уже показанный через stream; None — обычная отправка (сбрасывает флаг)."""
    val = _delivered.get()
    _delivered.set(None)
    return val


def telegram_stream_mark_cancelled() -> None:
    _cancelled.set(True)


def telegram_stream_was_cancelled() -> bool:
    return bool(_cancelled.get())


class TelegramStreamEditor:
    """Накопление текста и throttled edit_message_text."""

    def __init__(
        self,
        bot: Any,
        chat_id: int,
        message_id: int,
        *,
        show_stop: bool = True,
        show_reasoning: bool = False,
    ) -> None:
        self._bot = bot
        self._chat_id = int(chat_id)
        self._message_id = int(message_id)
        self._content_buf = ""
        self._reasoning_buf = ""
        self._last_edit = 0.0
        self._show_stop = show_stop
        self._show_reasoning = show_reasoning

    @property
    def text(self) -> str:
        from core.telegram_stream_reasoning import compose_stream_display

        return compose_stream_display(reasoning=self._reasoning_buf, content=self._content_buf)

    @property
    def content_only(self) -> str:
        return self._content_buf

    async def push_reasoning(self, delta: str) -> None:
        if not self._show_reasoning or not delta:
            return
        self._reasoning_buf += delta
        if len(self._reasoning_buf.strip()) < max(8, _stream_min_chars_before_edit() // 2):
            return
        await self._maybe_edit(force=False)

    async def push(self, delta: str) -> None:
        if not delta:
            return
        self._content_buf += delta
        if len(self.text.strip()) < _stream_min_chars_before_edit() and not self._content_buf.strip():
            return
        await self._maybe_edit(force=False)

    async def finalize(self, text: Optional[str] = None, *, remove_stop: bool = True) -> str:
        if text is not None:
            self._content_buf = text
        await self._maybe_edit(force=True, remove_stop=remove_stop)
        return self.text

    async def _maybe_edit(self, *, force: bool, remove_stop: bool = False) -> None:
        body = (self.text or "…").strip() or "…"
        display = body[:4080]
        now = time.monotonic()
        gap = _stream_min_edit_interval_sec()
        if not force and self._last_edit > 0 and (now - self._last_edit) < gap:
            return
        markup = None if remove_stop else (build_stop_keyboard(self._chat_id) if self._show_stop else None)
        try:
            await self._bot.edit_message_text(
                display,
                chat_id=self._chat_id,
                message_id=self._message_id,
                reply_markup=markup,
            )
            self._last_edit = now
        except Exception as e:
            logger.debug("telegram_stream edit: %s", e)


_PLACEHOLDER_REPLY = frozenset({"…", "...", ".", "…"})


def _stream_deliver_text(*, body: str, editor: "TelegramStreamEditor", show_reasoning: bool) -> str:
    content = (body or "").strip()
    if content and content not in _PLACEHOLDER_REPLY:
        return editor.text if show_reasoning else content
    if show_reasoning:
        composed = (editor.text or "").strip()
        if composed and composed not in _PLACEHOLDER_REPLY:
            return composed
    return ""


async def run_streaming_llm_to_telegram(
    *,
    llm: Any,
    gen_kw: Dict[str, Any],
    cancel_event: asyncio.Event,
    editor: TelegramStreamEditor,
) -> Dict[str, Any]:
    """generate_stream с on_delta → editor; возвращает dict как generate()."""
    content_chunks: list[str] = []
    edit_token = _editing.set(True)

    async def _on_content(piece: str) -> None:
        content_chunks.append(piece)
        await editor.push(piece)

    async def _on_reasoning(piece: str) -> None:
        await editor.push_reasoning(piece)

    try:
        gen = getattr(llm, "generate_stream", None)
        if gen is None:
            return await llm.generate(**gen_kw)

        stream_kw = dict(gen_kw)
        if editor._show_reasoning:
            stream_kw["on_reasoning_delta"] = _on_reasoning
        out = await gen(
            cancel_event=cancel_event,
            on_delta=_on_content,
            **stream_kw,
        )
        body = "".join(content_chunks) or str(out.get("content") or "")
        suffix = ""
        if cancel_event.is_set():
            telegram_stream_mark_cancelled()
            suffix = (os.getenv("TELEGRAM_STREAM_CANCEL_SUFFIX") or "⏹ Остановлено.").strip()
        if suffix and suffix not in body:
            body = (body.rstrip() + "\n\n" + suffix).strip() if body.strip() else suffix
        await editor.finalize(body, remove_stop=True)
        deliver = _stream_deliver_text(
            body=body,
            editor=editor,
            show_reasoning=bool(editor._show_reasoning),
        )
        ok = bool(body.strip()) and not (isinstance(out, dict) and out.get("error"))
        if deliver and ok and "TOOL_CALL:" not in body:
            _delivered.set(deliver)
            try:
                from core.telegram_progress import telegram_progress_disarm

                telegram_progress_disarm()
            except Exception as e:
                logger.debug("telegram_stream progress disarm: %s", e)
        return out if isinstance(out, dict) else {"content": body, "success": bool(body.strip())}
    finally:
        _editing.reset(edit_token)
