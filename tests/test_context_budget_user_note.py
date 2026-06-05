import unittest

from core.brain.context_budget import (
    prepend_context_budget_user_note,
    stash_context_budget_user_note,
)


class ContextBudgetUserNoteTests(unittest.TestCase):
    def test_stash_and_prepend_once(self) -> None:
        ctx: dict = {}
        stash_context_budget_user_note(
            ctx,
            prompt="x" * 13000,
            system_prompt="y" * 500,
        )
        self.assertIn("_context_budget_user_note", ctx)
        out = prepend_context_budget_user_note(ctx, "Ответ модели.")
        self.assertIn("/new", out)
        self.assertNotIn("_context_budget_user_note", ctx)
        again = prepend_context_budget_user_note(ctx, "Ответ модели.")
        self.assertEqual(again, "Ответ модели.")

    def test_no_note_under_limit(self) -> None:
        ctx: dict = {}
        stash_context_budget_user_note(ctx, prompt="короткий вопрос")
        self.assertNotIn("_context_budget_user_note", ctx)
