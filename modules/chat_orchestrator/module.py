import logging

from core.models import Output
from core.telegram_inline_meta import META_KEY
from core.brain import SILENT_DOCUMENT_USER_PROMPT, SILENT_IMAGE_USER_PROMPT, call_brain

logger = logging.getLogger(__name__)
from core.brain.text_helpers import TELEGRAM_PLAIN_REPLY_RULE
from core.model_profile import merge_system


class ChatOrchestratorModule:
    name = "chat-orchestrator"

    async def execute(self, args):
        """
        Главный диалоговый модуль.
        Все обычные сообщения идут сюда.
        """

        input_data = args.get("input", {}) or {}
        context = args.get("context", {}) or {}

        user_id = context.get("user_id")
        text = input_data.get("payload", "")
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()
        fc = context.get("file_context") if isinstance(context, dict) else {}
        meta_in = input_data.get("meta") if isinstance(input_data.get("meta"), dict) else {}
        doc_ctx = context.get("document_intake") if isinstance(context.get("document_intake"), dict) else {}
        if isinstance(fc, dict) and fc.get("error") == "size_limit_exceeded":
            return Output(
                type="text",
                payload="Файл слишком большой (лимит размера). Уменьшите изображение или пришлите другое.",
                meta={"module": "chat-orchestrator", "fallback": "file_too_large"},
            )
        has_image_path = (
            isinstance(fc, dict)
            and fc.get("file_type") == "image"
            and isinstance(fc.get("local_path"), str)
            and fc.get("local_path").strip()
        )
        fc_is_document = isinstance(fc, dict) and fc.get("file_type") == "document"
        has_doc_local = (
            fc_is_document
            and isinstance(fc.get("local_path"), str)
            and bool(fc.get("local_path", "").strip())
        )
        has_document_signal = has_doc_local or bool(doc_ctx) or fc_is_document
        att_flag = bool(meta_in.get("has_telegram_attachment") or context.get("has_telegram_attachment"))

        input_type = str(input_data.get("type") or "text")
        if not text and not has_image_path:
            if input_type == "image":
                return Output(
                    type="text",
                    payload="Не удалось принять изображение (загрузка или лимит). Попробуйте ещё раз или добавьте подпись к фото.",
                    meta={"module": "chat-orchestrator", "fallback": "image_unavailable"},
                )
            if has_document_signal:
                text = SILENT_DOCUMENT_USER_PROMPT
            elif att_flag:
                fn = (
                    meta_in.get("telegram_document_filename")
                    or context.get("telegram_document_filename")
                    or ""
                )
                suffix = f" ({fn})" if fn else ""
                return Output(
                    type="text",
                    payload=(
                        f"Вложение{suffix} без текстовой подписи не обработано: файл не скачан или приём файлов отключён "
                        "(FILE_INTAKE_ENABLED). Добавьте короткий текст к сообщению или проверьте лимиты на сервере."
                    ),
                    meta={"module": "chat-orchestrator", "fallback": "attachment_no_file_context"},
                )
            else:
                return Output(
                    type="text",
                    payload="Пустой запрос. Напиши сообщение или используй команду.",
                    meta={"module": "chat-orchestrator", "fallback": "empty_payload"},
                )
        if not text and has_image_path:
            text = SILENT_IMAGE_USER_PROMPT

        # Вызов мозга
        try:
            reply = await call_brain(
                user_text=text,
                context=context,
                system_prompt=merge_system(
                    "Ты универсальный социальный ассистент на расширяемой платформе. Отвечай по-русски живым естественным языком, "
                    "без канцелярита; сначала прямой ответ на вопрос, потом детали по необходимости. "
                    "Инструменты — только когда нужны (ссылка, учебник, разбор сайта). "
                    "Не выдумывай имена slash-команды и модулей: опирайся на блок telegram_commands_catalog в контексте и список доступных инструментов. "
                    "Новый плагин — только если в списке инструментов есть SelfProgramming.generate_module и пользователь явно просит новую возможность; "
                    "соблюдай контракт в системной инструкции агента (slash vs intent/capabilities, эталон modules/echo). "
                    "Учитывай память и контекст диалога.",
                    TELEGRAM_PLAIN_REPLY_RULE,
                ),
            )
        except Exception:
            logger.exception(
                "[chat_orchestrator] call_brain failed user_id=%s",
                user_id,
            )
            reply = "Извини, сейчас не получилось обработать сообщение. Попробуй еще раз."
        try:
            from core.brain.response_finalize import finalize_user_reply

            reply = finalize_user_reply(reply or "", user_text=text) or ""
        except Exception:
            reply = (reply or "").strip()
        reply = (reply or "").strip()
        if not reply:
            try:
                from core.behavior_store import BehaviorStore
                from core.empty_reply_recovery import empty_reply_user_message, recover_empty_chat_reply

                _uid = str(user_id or "").strip()
                _gid = context.get("group_id")
                _rec = BehaviorStore().load(_uid, _gid) if _uid else {}
                _ctx = dict(context)
                _ctx["behavior_record"] = _rec
                if not isinstance(_ctx.get("recent_messages"), list):
                    _ctx["recent_messages"] = _rec.get("recent_messages")
                recovered = await recover_empty_chat_reply(user_text=text, context=_ctx)
                if recovered and recovered.strip():
                    reply = recovered.strip()
                else:
                    reply = empty_reply_user_message(recovered=False)
            except Exception:
                reply = (
                    "Пустой ответ от модели. Повтори сообщение или задай "
                    "OPENROUTER_MODEL_FREE на конкретную модель (см. openrouter.ai/models)."
                )

        from core.geo_reply_tokens import expand_telegram_geo_placeholders

        reply, geo_meta = await expand_telegram_geo_placeholders(reply)

        out_meta = {"module": "chat-orchestrator"}
        if isinstance(context, dict) and context.get("operational_diag_short_circuit"):
            out_meta["operational_diag_short_circuit"] = True
        inline_rows: list = []
        if isinstance(context, dict):
            if context.get("image_output_path"):
                out_meta["image_output_path"] = context.get("image_output_path")
            if context.get("image_operation"):
                out_meta["image_operation"] = context.get("image_operation")
            if context.get("ocr_text"):
                out_meta["ocr_text"] = context.get("ocr_text")
            kb = context.pop(META_KEY, None)
            if isinstance(kb, list):
                inline_rows = [list(r) for r in kb if isinstance(r, list)]
        for _gk, _gv in geo_meta.items():
            if _gv is not None:
                out_meta[_gk] = _gv
        pending = (context.get("pending_doc_id") or "").strip() if isinstance(context, dict) else ""
        if pending:
            from core.user_document_pending import pending_document_keyboard_rows

            extra = pending_document_keyboard_rows(pending)
            if extra:
                inline_rows = inline_rows + extra
        if inline_rows:
            out_meta[META_KEY] = inline_rows

        return Output(
            type="text",
            payload=reply,
            meta=out_meta
        )
