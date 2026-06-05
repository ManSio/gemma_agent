from __future__ import annotations

from typing import Any

from aiogram.types import Message

import logging

from core.error_analysis import record_error_event
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


def register(layer: Any) -> None:
    dp = layer.dp

    @dp.message()
    async def handle_message(message: Message):
        if not (
            message.text
            or message.photo
            or message.document
            or message.video
            or message.voice
            or message.location
        ):
            return
        if message.from_user is None:
            logger.warning(
                "skip message without from_user (chat_id=%s message_id=%s)",
                getattr(message.chat, "id", None),
                getattr(message, "message_id", None),
            )
            MONITOR.inc("input_skipped_no_actor_total")
            return
        await layer._ensure_bot_identity()
        if layer._is_group_chat(message) and not layer._should_process_group_message(message):
            try:
                from core.group_transcript import enabled as _gt_enabled
                from core.group_transcript import record_skipped_group_message

                if _gt_enabled():
                    record_skipped_group_message(message)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'messages', e, exc_info=True)
            logger.debug(
                "skip group message: need /command, @bot, or reply to bot (chat_id=%s)",
                message.chat.id,
            )
            return

        text_override = None
        if message.voice:
            if not (layer._voice.enabled and layer._voice.stt_enabled):
                await message.answer(
                    "Голос выключен. Универсальный режим: включите VOICE_ENABLED=true и VOICE_STT_ENABLED=true "
                    "и задайте облачный STT (рекомендуется) или локальный vosk/whisper.cpp — см. docs/OPERATIONS_AND_ADMIN.md."
                )
                return
            v = message.voice
            size = int(getattr(v, "file_size", 0) or 0)
            if not layer._file_intake.enforce_size_limit("voice", size):
                await message.answer("Голосовое сообщение слишком большое.")
                return
            voice_path = ""
            try:
                voice_path = await layer._file_intake.download_file(layer.bot, v.file_id, "voice.ogg")
                if not voice_path:
                    await message.answer("Не удалось скачать голосовое.")
                    return
                text_override = (await layer._voice.stt(voice_path)).strip()
            except Exception as e:
                record_error_event("voice", "voice download/stt failed", exc=e)
                await message.answer("Ошибка при распознавании речи.")
                return
            finally:
                layer._file_intake.cleanup(voice_path or None)
            if not text_override:
                await message.answer(
                    "Пустой текст после STT. "
                    + layer._voice.stt_empty_operator_hint()
                )
                return

        await layer._process_message(message, text_override=text_override)
