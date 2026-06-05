"""Регрессия: фоновые задачи логируют исключения, а не теряют их."""
from __future__ import annotations

import asyncio
import logging
import unittest

import core.async_spawn as async_spawn_mod
from core.async_spawn import spawn_logged


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class AsyncSpawnTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_logged_logs_exception(self) -> None:
        async def _boom() -> None:
            raise ValueError("spawn test boom")

        handler = _ListHandler()
        log = async_spawn_mod.logger
        log.addHandler(handler)
        log.setLevel(logging.ERROR)
        task = spawn_logged(_boom(), label="test_boom")
        for _ in range(50):
            if task.done():
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(task.done())

        errors = [r for r in handler.records if r.levelno >= logging.ERROR]
        log.removeHandler(handler)
        self.assertTrue(errors, "expected ERROR log from failed background task")
        self.assertIn("test_boom", errors[0].getMessage())
        self.assertIn("spawn test boom", errors[0].getMessage())


if __name__ == "__main__":
    unittest.main()
