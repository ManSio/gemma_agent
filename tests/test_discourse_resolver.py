"""Тесты discourse resolver — эллипсис, наследование, регрессии."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.brain.discourse_resolver import (
    ACTION_STAY,
    apply_discourse_to_context,
    resolve_discourse,
    structural_thread_continuation,
)
from core.brain.dialogue_context import build_dsv
from core.brain.profile_registry import is_continuation_turn, resolve_continuation_profile


def _ai_ctx(user: str) -> dict:
    """Контекст диалога про ИИ (prod incident)."""
    last_a = (
        "Термин «искусственный интеллект» придумали в 1956 году на конференции в Дартмуте. "
        "Название осталось исторически, хотя современные системы не обладают человеческим "
        "интеллектом в полном смысле."
    )
    prev_u = "почему назвали ИИ сейчас если у него сейчас нет интеллекта"
    rd = [
        {"role": "user", "text": prev_u},
        {"role": "assistant", "text": last_a},
        {"role": "user", "text": user},
    ]
    return {
        "user_text": user,
        "recent_dialogue": rd,
        "dialogue_state": {
            "last_intent": "explain",
            "last_brain_profile": "quick_explain",
        },
    }


class DiscourseResolverTests(unittest.TestCase):
    def test_prod_ellipsis_inherits_thread(self) -> None:
        user = "как бы ты сейчас назвал правильно"
        ctx = _ai_ctx(user)
        inherit, reason = structural_thread_continuation(user, ctx)
        self.assertTrue(inherit, reason)
        res = resolve_discourse(user, ctx)
        self.assertEqual(res.action, ACTION_STAY)
        self.assertEqual(res.inherit_intent, "explain")
        self.assertEqual(res.inherit_profile, "quick_explain")
        self.assertTrue(res.rewrite_applied)
        self.assertIn("предыдущему вопросу", res.effective_user_text)

    def test_news_branch_not_inherit(self) -> None:
        user = "что нового в иране"
        ctx = _ai_ctx(user)
        inherit, _ = structural_thread_continuation(user, ctx)
        self.assertFalse(inherit)

    def test_substantive_new_question_not_inherit(self) -> None:
        user = "почему трава зеленая"
        ctx = _ai_ctx(user)
        inherit, reason = structural_thread_continuation(user, ctx)
        self.assertFalse(inherit)
        self.assertEqual(reason, "substantive_question")

    def test_correction_tone_not_inherit(self) -> None:
        user = "не то мы про ии говорили"
        ctx = _ai_ctx(user)
        inherit, reason = structural_thread_continuation(user, ctx)
        self.assertFalse(inherit)
        self.assertTrue(reason.startswith("tone:"))

    def test_explicit_continuation_davay(self) -> None:
        user = "давай"
        ctx = _ai_ctx(user)
        inherit, _ = structural_thread_continuation(user, ctx)
        self.assertTrue(inherit)

    def test_dsv_includes_intent_and_profile(self) -> None:
        ctx = _ai_ctx("как бы ты сейчас назвал правильно")
        dsv = build_dsv(ctx)
        prompt = dsv.to_prompt()
        self.assertIn("last_intent=explain", prompt)
        self.assertIn("last_profile=quick_explain", prompt)

    def test_apply_idempotent(self) -> None:
        ctx = _ai_ctx("как бы ты сейчас назвал правильно")
        t1, c1 = apply_discourse_to_context("как бы ты сейчас назвал правильно", ctx)
        t2, c2 = apply_discourse_to_context(t1, c1)
        self.assertEqual(t1, t2)
        self.assertTrue(c2.get("_discourse_applied"))

    def test_apply_preserves_context_identity(self) -> None:
        ctx = _ai_ctx("как бы ты сейчас назвал правильно")
        _, out = apply_discourse_to_context("как бы ты сейчас назвал правильно", ctx)
        self.assertIs(out, ctx)

    def test_profile_registry_continuation(self) -> None:
        ctx = _ai_ctx("как бы ты сейчас назвал правильно")
        _, ctx = apply_discourse_to_context("как бы ты сейчас назвал правильно", ctx)
        self.assertTrue(is_continuation_turn("как бы ты сейчас назвал правильно", ctx))
        prof = resolve_continuation_profile("как бы ты сейчас назвал правильно", ctx)
        self.assertEqual(prof, "quick_explain")

    def test_orchestrator_intent_inherit(self) -> None:
        import tempfile

        from core.orchestrator import Orchestrator
        from core.plugin_registry import PluginRegistry
        from core.policy_engine import PolicyEngine

        with tempfile.TemporaryDirectory() as td:
            o = Orchestrator(plugin_registry=PluginRegistry(td), policy_engine=PolicyEngine())
            ctx = _ai_ctx("как бы ты сейчас назвал правильно")
            intent = o._detect_intent(
                "как бы ты сейчас назвал правильно",
                {"dialogue_state": ctx["dialogue_state"], "recent_messages": ctx["recent_dialogue"]},
                planner_context=ctx,
            )
            self.assertEqual(intent, "explain")

    def test_batch_blocks_discourse_inherit(self) -> None:
        user = "давай"
        ctx = _ai_ctx(user)
        ctx["brain_force_batch_profile"] = True
        inherit, reason = structural_thread_continuation(user, ctx)
        self.assertFalse(inherit)
        self.assertEqual(reason, "batch_continuation")

    def test_strip_ephemeral_dialogue_state(self) -> None:
        from core.brain.discourse_resolver import strip_ephemeral_discourse_state

        ds = {
            "last_intent": "explain",
            "_discourse_inherit_intent": "explain",
            "_discourse_inherit_profile": "quick_explain",
        }
        clean = strip_ephemeral_discourse_state(ds)
        self.assertEqual(clean.get("last_intent"), "explain")
        self.assertNotIn("_discourse_inherit_intent", clean)
        self.assertNotIn("_discourse_inherit_profile", clean)


        with patch.dict(os.environ, {"DISCOURSE_RESOLVER_ENABLED": "false"}, clear=False):
            inherit, reason = structural_thread_continuation(
                "как бы ты сейчас назвал правильно",
                _ai_ctx("как бы ты сейчас назвал правильно"),
            )
            self.assertFalse(inherit)
            self.assertEqual(reason, "disabled")

    def test_judge_upgrade_after_sync_apply(self) -> None:
        import asyncio

        from core.brain.discourse_resolver import apply_discourse_to_context_async

        ctx = _ai_ctx("как бы ты сейчас назвал правильно")
        _, ctx = apply_discourse_to_context("как бы ты сейчас назвал правильно", ctx)
        dr = dict(ctx.get("discourse_resolution") or {})
        dr["reason"] = "structural"
        dr["judge_source"] = "structural"
        dr["continuation"] = True
        ctx["discourse_resolution"] = dr

        async def _fake_async(raw, base, *, llm=None):
            return resolve_discourse(raw, base)

        async def _run() -> tuple:
            return await apply_discourse_to_context_async(
                "как бы ты сейчас назвал правильно",
                ctx,
                llm=object(),
            )

        with patch(
            "core.brain.discourse_resolver.resolve_discourse_async",
            side_effect=_fake_async,
        ) as mocked:
            _text, out = asyncio.run(_run())
            mocked.assert_called_once()
            self.assertTrue(out.get("_discourse_applied"))
            self.assertIsInstance(out.get("discourse_audit"), dict)


class ContextDeprioritizeTests(unittest.TestCase):
    def test_deprioritize_failed_assistant_turn(self) -> None:
        from core.context_compression import deprioritize_failed_dialogue_rows

        rows = [
            {"role": "user", "text": "q1"},
            {"role": "assistant", "text": "Сейчас ответ не сложился. Повтори запрос."},
            {"role": "user", "text": "q2"},
            {"role": "assistant", "text": "ok answer"},
            {"role": "user", "text": "q3"},
            {"role": "assistant", "text": "tail"},
        ]
        out = deprioritize_failed_dialogue_rows(rows, keep_tail=4)
        texts = [r.get("text") for r in out]
        self.assertNotIn("Сейчас ответ не сложился. Повтори запрос.", texts)
        self.assertIn("tail", texts)


if __name__ == "__main__":
    unittest.main()
