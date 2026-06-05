from __future__ import annotations

from typing import Any

from core.input_handlers import callbacks, commands_admin, commands_basic, commands_user, messages
from core.input_handlers.ack_middleware import SlashCommandFeedbackMiddleware


def register_all_handlers(layer: Any) -> None:
    layer.dp.message.middleware(SlashCommandFeedbackMiddleware())
    commands_basic.register(layer)
    commands_admin.register(layer)
    commands_user.register(layer)
    callbacks.register(layer)
    messages.register(layer)
