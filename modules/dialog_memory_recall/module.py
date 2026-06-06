"""Slash: сводка из архива переписки, session digest и Mem0; опционально LLM-сжатие."""

from __future__ import annotations

import logging
import os

from core.brain import call_brain

logger = logging.getLogger(__name__)
from core.dialog_memory_recall import recall_help_text
from core.light_slash import parse_slash_args
from core.memory_recall_facade import build_slash_recall_bundle, plain_text_requests_dialog_recall
from core.models import Output


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_archive_n() -> int:
    try:
        return max(8, min(120, int((os.getenv("DIALOG_RECALL_ARCHIVE_DEFAULT") or "28").strip() or "28")))
    except ValueError:
        return 28


def _llm_max_chars() -> int:
    try:
        return max(2000, min(24000, int((os.getenv("DIALOG_RECALL_LLM_MAX_CHARS") or "9000").strip() or "9000")))
    except ValueError:
        return 9000


def _parse_recall_subcommand(rest: str) -> tuple[str, str, int]:
    mode = "summary"
    query = ""
    archive_n = _default_archive_n()
    r = (rest or "").strip()
    if not r:
        return mode, query, archive_n
    head, sep, tail = r.partition(" ")
    head_l = head.strip().lower()
    tail_s = tail.strip()
    if head_l == "archive":
        mode = "archive"
        if tail_s:
            try:
                archive_n = max(4, min(150, int(tail_s.split()[0])))
            except ValueError:
                pass
    elif head_l == "search":
        mode = "search"
        query = tail_s
    elif head_l in ("когда", "when"):
        mode = "when"
        query = tail_s
    elif head_l == "summary":
        mode = "summary"
    else:
        mode = "summary"
    return mode, query, archive_n


class DialogMemoryRecallModule:
    async def execute(self, args: dict):
        if not _truthy("DIALOG_MEMORY_RECALL_ENABLED", True):
            return Output(
                type="text",
                payload="Плагин dialog_memory_recall отключён (DIALOG_MEMORY_RECALL_ENABLED=false).",
                meta={"module": "dialog_memory_recall", "skipped": True},
            )

        input_data = args.get("input") or {}
        context = args.get("context") or {}
        if not isinstance(context, dict):
            context = {}

        uid = str(context.get("user_id") or "").strip()
        gid = context.get("group_id")
        if gid is not None and str(gid).strip() == "":
            gid = None

        payload = str(input_data.get("payload") or "")
        cmd, rest = parse_slash_args(payload)
        if cmd != "dialog_recall":
            if plain_text_requests_dialog_recall(payload):
                rest = "summary"
            else:
                return Output(
                    type="text",
                    payload=recall_help_text(),
                    meta={"module": "dialog_memory_recall"},
                )

        rest = (rest or "").strip()
        if not uid:
            return Output(
                type="text",
                payload="Нет user_id в контексте — команда доступна из чата с привязкой пользователя.",
                meta={"module": "dialog_memory_recall"},
            )

        if rest.lower() in ("help", "?"):
            return Output(
                type="text",
                payload=recall_help_text(),
                meta={"module": "dialog_memory_recall"},
            )

        head, sep, tail = rest.partition(" ")
        head_l = head.strip().lower()
        tail_s = tail.strip()

        if head_l in ("llm", "compress", "сжать"):
            if not _truthy("DIALOG_RECALL_LLM_ENABLED", False):
                return Output(
                    type="text",
                    payload="LLM-сжатие recall отключено (DIALOG_RECALL_LLM_ENABLED=false).",
                    meta={"module": "dialog_memory_recall", "skipped": True},
                )
            sub_mode, sub_query, sub_n = _parse_recall_subcommand(tail_s)
            facts = build_slash_recall_bundle(
                user_id=uid,
                group_id=gid,
                context=context,
                mode=sub_mode,
                query=sub_query,
                archive_tail=sub_n,
            )
            if not facts.strip() or facts.strip() == "(Нечего показать.)":
                return Output(
                    type="text",
                    payload="Недостаточно фактов для пересказа (архив пуст или режим не дал данных).",
                    meta={"module": "dialog_memory_recall", "recall_mode": "llm", "recall_sub": sub_mode},
                )
            if facts.strip().startswith("Укажи ") or facts.strip().startswith("Не разобрал"):
                return Output(
                    type="text",
                    payload=facts[:12000],
                    meta={"module": "dialog_memory_recall", "recall_mode": "llm", "recall_sub": sub_mode},
                )
            cap = _llm_max_chars()
            facts_trim = facts[:cap]
            brain_ctx = dict(context)
            brain_ctx["brain_skip_memory_fetch"] = True
            system_prompt = (
                "Ты помогаешь по сжатым фактам из памяти бота. Сделай связный краткий пересказ "
                "(1–5 предложений или короткий список). Не добавляй факты вне входного блока. "
                "Язык ответа — как у пользователя."
            )
            user_augmented = (
                "Сожми для человека только по этим данным (не цитируй служебные заголовки дословно):\n\n" + facts_trim
            )
            try:
                reply = await call_brain(user_augmented, brain_ctx, system_prompt)
            except Exception:
                logger.exception(
                    "[dialog_memory_recall] llm compress failed user_id=%s mode=%s",
                    uid,
                    sub_mode,
                )
                return Output(
                    type="text",
                    payload=(
                        "LLM-сжатие недоступно — показываю факты без пересказа:\n\n" + facts_trim
                    )[:12000],
                    meta={
                        "module": "dialog_memory_recall",
                        "recall_mode": "llm",
                        "recall_sub": sub_mode,
                        "llm_fallback": "facts_only",
                    },
                )
            return Output(
                type="text",
                payload=(reply or facts_trim or "(пустой ответ модели)")[:12000],
                meta={"module": "dialog_memory_recall", "recall_mode": "llm", "recall_sub": sub_mode},
            )

        mode, query, archive_n = _parse_recall_subcommand(rest)

        body = build_slash_recall_bundle(
            user_id=uid,
            group_id=gid,
            context=context,
            mode=mode,
            query=query,
            archive_tail=archive_n,
        )
        return Output(
            type="text",
            payload=body[:12000],
            meta={"module": "dialog_memory_recall", "recall_mode": mode},
        )
