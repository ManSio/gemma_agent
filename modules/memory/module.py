"""
Memory Module - slash /mem_* с записью в Mem0 (brain) + резерв facts.json.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.memory_slash_bridge import forget_fact, recall_facts, remember_fact
from core.models import Output


class MemoryModule:
    """Долговременные факты: Mem0 — основной источник; slash-store — fallback."""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.storage_path = self.config.get("storage_path", "./data/memory")

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input", {})
        payload = input_data.get("payload", "")
        ctx = args.get("context") if isinstance(args.get("context"), dict) else {}
        user_id = ctx.get("user_id")

        if payload.startswith("/mem_remember "):
            fact = payload[len("/mem_remember ") :].strip()
            if not fact:
                return [
                    Output(
                        type="text",
                        payload="Укажите текст после /mem_remember",
                        meta={"module": "memory", "action": "remember", "ok": False},
                    )
                ]
            ok, backend = remember_fact(user_id, fact, self.storage_path)
            if not ok:
                return [
                    Output(
                        type="text",
                        payload="Не удалось сохранить факт (см. bot.log).",
                        meta={"module": "memory", "action": "remember", "ok": False},
                    )
                ]
            hint = (
                "Mem0 (как в диалоге)"
                if backend == "mem0"
                else "локальный резерв (нет user_id или Mem0)"
            )
            short = f"{fact[:50]}{'…' if len(fact) > 50 else ''}"
            return [
                Output(
                    type="text",
                    payload=f"Факт запомнен ({hint}): {short}",
                    meta={
                        "module": "memory",
                        "action": "remember",
                        "ok": True,
                        "backend": backend,
                    },
                )
            ]

        if payload.startswith("/mem_recall"):
            lines, backend = recall_facts(user_id, self.storage_path)
            if lines:
                body = "\n".join(lines)
                return [
                    Output(
                        type="text",
                        payload=f"Факты ({backend}):\n{body}",
                        meta={"module": "memory", "action": "recall", "backend": backend},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload="Нет запомненных фактов",
                    meta={"module": "memory", "action": "recall", "backend": backend},
                )
            ]

        if payload.startswith("/mem_forget"):
            fact = payload[len("/mem_forget") :].strip()
            if not fact:
                return [
                    Output(
                        type="text",
                        payload="Укажите текст после /mem_forget",
                        meta={"module": "memory", "action": "forget", "ok": False},
                    )
                ]
            ok, backend, mem_n = forget_fact(user_id, fact, self.storage_path)
            short = f"{fact[:50]}{'…' if len(fact) > 50 else ''}"
            if ok:
                msg = f"Забыто ({backend}): {short}"
                if mem_n:
                    msg += f" [Mem0: {mem_n}]"
            else:
                msg = "Факт не найден ни в slash-store, ни в Mem0 (по этой фразе)."
            return [
                Output(
                    type="text",
                    payload=msg,
                    meta={
                        "module": "memory",
                        "action": "forget",
                        "ok": ok,
                        "backend": backend,
                        "mem0_deleted": mem_n,
                    },
                )
            ]

        return [
            Output(
                type="text",
                payload=(
                    "Команды:\n"
                    "/mem_remember <факт> — в Mem0 (+ резерв)\n"
                    "/mem_recall — Mem0 и резерв\n"
                    "/mem_forget <факт> — Mem0 + slash-store"
                ),
                meta={"module": "memory"},
            )
        ]
